"""
scanner_agent.agents.bus  v1.0.0

AgentBus — capa de comunicacion entre agentes basada en SQLite.

El bus es el unico estado compartido entre agentes.  Cada agente
lee y escribe en esta base de datos para coordinar trabajo.

Tablas:
    audit_sessions  — una sesion de auditoria agrupa todas las tareas
    agent_tasks     — tareas encoladas y su estado
    agent_findings  — hallazgos producidos por los scanner agents
    agent_messages  — mensajes punto a punto entre agentes

Uso tipico:
    bus = AgentBus("reports/agent_bus.db")
    session_id = bus.open_session(domains=["ELECTORAL","NOMINA"], started_by="CLI")
    task_id = bus.enqueue_task(session_id, agent_type="scanner", domain="ELECTORAL", payload={...})
    bus.claim_task(task_id)
    bus.complete_task(task_id, result={...})
    bus.post_finding(session_id, task_id, finding)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Esquema ───────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_sessions (
    session_id      TEXT PRIMARY KEY,
    started_by      TEXT NOT NULL DEFAULT 'CLI',
    domains         TEXT NOT NULL DEFAULT '[]',   -- JSON array
    status          TEXT NOT NULL DEFAULT 'open', -- open|scanning|reviewing|reporting|completed|failed
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    total_findings  INTEGER DEFAULT 0,
    reviewed_findings INTEGER DEFAULT 0,
    certified_findings INTEGER DEFAULT 0,
    notes           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id         TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent_type      TEXT NOT NULL,  -- scanner|review|report|orchestrator
    domain          TEXT,           -- ELECTORAL|NOMINA|CONTRATOS|...
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|in_progress|completed|failed|cancelled
    priority        INTEGER DEFAULT 5,
    payload         TEXT DEFAULT '{}',   -- JSON: parametros de la tarea
    result          TEXT DEFAULT '{}',   -- JSON: resultado
    error           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    parent_task_id  TEXT,           -- para subtareas
    FOREIGN KEY (session_id) REFERENCES audit_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS agent_findings (
    finding_id      TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    source_agent    TEXT NOT NULL,  -- "scanner:ELECTORAL", "scanner:NOMINA", ...
    domain          TEXT NOT NULL,
    flag            TEXT NOT NULL,  -- AuditFlag constant
    confidence      REAL NOT NULL,
    record_count    INTEGER DEFAULT 0,
    records_json    TEXT DEFAULT '[]',  -- JSON array de RowRecord dicts
    notes           TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',  -- pending|reviewed|certified|dismissed
    dismissed_reason TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT,
    reviewed_by     TEXT,
    certified_at    TEXT,
    FOREIGN KEY (session_id) REFERENCES audit_sessions(session_id),
    FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id)
);

CREATE TABLE IF NOT EXISTS agent_messages (
    msg_id          TEXT PRIMARY KEY,
    session_id      TEXT,
    from_agent      TEXT NOT NULL,
    to_agent        TEXT NOT NULL,  -- "*" para broadcast
    msg_type        TEXT NOT NULL,  -- task_complete|finding_ready|review_done|status|error
    payload         TEXT DEFAULT '{}',  -- JSON
    sent_at         TEXT NOT NULL,
    read_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_session  ON agent_tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON agent_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type     ON agent_tasks(agent_type);
CREATE INDEX IF NOT EXISTS idx_findings_session ON agent_findings(session_id);
CREATE INDEX IF NOT EXISTS idx_findings_status  ON agent_findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_domain  ON agent_findings(domain);
CREATE INDEX IF NOT EXISTS idx_msgs_to         ON agent_messages(to_agent, read_at);
"""


# ── Dataclasses de transporte ─────────────────────────────────────────────

@dataclass
class AuditSession:
    session_id: str
    started_by: str
    domains: list[str]
    status: str
    started_at: str
    completed_at: Optional[str] = None
    total_findings: int = 0
    reviewed_findings: int = 0
    certified_findings: int = 0
    notes: str = ""


@dataclass
class AgentTask:
    task_id: str
    session_id: str
    agent_type: str
    domain: Optional[str]
    status: str
    priority: int
    payload: dict[str, Any]
    result: dict[str, Any]
    error: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    parent_task_id: Optional[str] = None


@dataclass
class AgentFinding:
    """
    Un hallazgo producido por un DomainScannerAgent.
    Se publica al bus y el ReviewAgent lo procesa.
    """
    finding_id: str
    session_id: str
    task_id: str
    source_agent: str          # "scanner:ELECTORAL"
    domain: str                # AuditDomain constant
    flag: str                  # AuditFlag constant
    confidence: float
    records: list[dict]        # lista de RowRecord.to_dict()
    notes: str = ""
    status: str = "pending"   # pending|reviewed|certified|dismissed
    dismissed_reason: str = ""
    created_at: str = field(default_factory=lambda: _now())
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    certified_at: Optional[str] = None

    @property
    def record_count(self) -> int:
        return len(self.records)


# ── Helpers ───────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


# ── AgentBus ──────────────────────────────────────────────────────────────

class AgentBus:
    """
    Canal de comunicacion entre agentes.

    Todos los metodos son thread-safe (WAL mode + check_same_thread=False).
    En produccion con multiples procesos, usar un solo proceso con
    threading o sustituir por PostgreSQL / Redis Streams.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,    # autocommit
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        logger.info("AgentBus iniciado: %s", self.db_path)

    def close(self):
        self._conn.close()

    # ── Sesiones ─────────────────────────────────────────────────────────

    def open_session(
        self,
        domains: list[str],
        started_by: str = "CLI",
        notes: str = "",
    ) -> str:
        """Abre una nueva sesion de auditoria. Retorna session_id."""
        session_id = _uid()
        self._conn.execute(
            """INSERT INTO audit_sessions
               (session_id, started_by, domains, status, started_at, notes)
               VALUES (?,?,?,?,?,?)""",
            (session_id, started_by, json.dumps(domains), "open", _now(), notes),
        )
        logger.info("Sesion abierta: %s dominios=%s", session_id[:8], domains)
        return session_id

    def update_session_status(self, session_id: str, status: str) -> None:
        completed_at = _now() if status in ("completed", "failed") else None
        self._conn.execute(
            "UPDATE audit_sessions SET status=?, completed_at=? WHERE session_id=?",
            (status, completed_at, session_id),
        )

    def get_session(self, session_id: str) -> Optional[AuditSession]:
        row = self._conn.execute(
            "SELECT * FROM audit_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return AuditSession(
            session_id=row["session_id"],
            started_by=row["started_by"],
            domains=json.loads(row["domains"]),
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            total_findings=row["total_findings"],
            reviewed_findings=row["reviewed_findings"],
            certified_findings=row["certified_findings"],
            notes=row["notes"] or "",
        )

    def list_sessions(self, limit: int = 20) -> list[AuditSession]:
        rows = self._conn.execute(
            "SELECT * FROM audit_sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            AuditSession(
                session_id=r["session_id"],
                started_by=r["started_by"],
                domains=json.loads(r["domains"]),
                status=r["status"],
                started_at=r["started_at"],
                completed_at=r["completed_at"],
                total_findings=r["total_findings"],
                reviewed_findings=r["reviewed_findings"],
                certified_findings=r["certified_findings"],
                notes=r["notes"] or "",
            )
            for r in rows
        ]

    # ── Tareas ────────────────────────────────────────────────────────────

    def enqueue_task(
        self,
        session_id: str,
        agent_type: str,
        payload: dict[str, Any],
        domain: Optional[str] = None,
        priority: int = 5,
        parent_task_id: Optional[str] = None,
    ) -> str:
        """Encola una tarea. Retorna task_id."""
        task_id = _uid()
        self._conn.execute(
            """INSERT INTO agent_tasks
               (task_id, session_id, agent_type, domain, status, priority,
                payload, result, error, created_at, parent_task_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, session_id, agent_type, domain, "pending", priority,
                json.dumps(payload), "{}", "", _now(), parent_task_id,
            ),
        )
        logger.debug("Tarea encolada: %s type=%s domain=%s", task_id[:8], agent_type, domain)
        return task_id

    def claim_task(self, task_id: str) -> bool:
        """
        Marca la tarea como in_progress.
        Retorna True si la reclamacion fue exitosa (otra instancia no la tomo).
        """
        cur = self._conn.execute(
            """UPDATE agent_tasks SET status='in_progress', started_at=?
               WHERE task_id=? AND status='pending'""",
            (_now(), task_id),
        )
        return cur.rowcount == 1

    def complete_task(
        self,
        task_id: str,
        result: dict[str, Any],
        status: str = "completed",
        error: str = "",
    ) -> None:
        self._conn.execute(
            """UPDATE agent_tasks
               SET status=?, result=?, error=?, completed_at=?
               WHERE task_id=?""",
            (status, json.dumps(result), error, _now(), task_id),
        )

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        row = self._conn.execute(
            "SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def get_pending_tasks(
        self,
        agent_type: str,
        session_id: Optional[str] = None,
    ) -> list[AgentTask]:
        """Retorna tareas pendientes para un tipo de agente, ordenadas por prioridad."""
        if session_id:
            rows = self._conn.execute(
                """SELECT * FROM agent_tasks
                   WHERE agent_type=? AND status='pending' AND session_id=?
                   ORDER BY priority ASC, created_at ASC""",
                (agent_type, session_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM agent_tasks
                   WHERE agent_type=? AND status='pending'
                   ORDER BY priority ASC, created_at ASC""",
                (agent_type,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def session_tasks_done(self, session_id: str, agent_type: str) -> bool:
        """True si todas las tareas del tipo dado en la sesion estan completadas."""
        row = self._conn.execute(
            """SELECT COUNT(*) as cnt FROM agent_tasks
               WHERE session_id=? AND agent_type=? AND status NOT IN ('completed','failed','cancelled')""",
            (session_id, agent_type),
        ).fetchone()
        return row["cnt"] == 0

    def _row_to_task(self, row: sqlite3.Row) -> AgentTask:
        return AgentTask(
            task_id=row["task_id"],
            session_id=row["session_id"],
            agent_type=row["agent_type"],
            domain=row["domain"],
            status=row["status"],
            priority=row["priority"],
            payload=json.loads(row["payload"] or "{}"),
            result=json.loads(row["result"] or "{}"),
            error=row["error"] or "",
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
        )

    # ── Findings ──────────────────────────────────────────────────────────

    def post_finding(self, finding: AgentFinding) -> str:
        """Publica un hallazgo al bus. Retorna finding_id."""
        self._conn.execute(
            """INSERT INTO agent_findings
               (finding_id, session_id, task_id, source_agent, domain, flag,
                confidence, record_count, records_json, notes, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                finding.finding_id,
                finding.session_id,
                finding.task_id,
                finding.source_agent,
                finding.domain,
                finding.flag,
                finding.confidence,
                finding.record_count,
                json.dumps(finding.records),
                finding.notes,
                finding.status,
                finding.created_at,
            ),
        )
        # Actualizar contador en la sesion
        self._conn.execute(
            "UPDATE audit_sessions SET total_findings = total_findings + 1 WHERE session_id=?",
            (finding.session_id,),
        )
        logger.debug(
            "Finding publicado: %s flag=%s conf=%.2f",
            finding.finding_id[:8], finding.flag, finding.confidence,
        )
        return finding.finding_id

    def get_findings(
        self,
        session_id: str,
        status: Optional[str] = None,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
    ) -> list[AgentFinding]:
        """Retorna findings de una sesion con filtros opcionales."""
        conditions = ["session_id=?"]
        params: list[Any] = [session_id]
        if status:
            conditions.append("status=?")
            params.append(status)
        if domain:
            conditions.append("domain=?")
            params.append(domain)
        if min_confidence > 0:
            conditions.append("confidence>=?")
            params.append(min_confidence)

        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"SELECT * FROM agent_findings WHERE {where} ORDER BY confidence DESC",
            params,
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def update_finding_status(
        self,
        finding_id: str,
        status: str,
        reviewed_by: str = "ReviewAgent",
        dismissed_reason: str = "",
    ) -> None:
        now = _now()
        if status == "certified":
            self._conn.execute(
                """UPDATE agent_findings
                   SET status=?, reviewed_at=?, reviewed_by=?, certified_at=?, dismissed_reason=?
                   WHERE finding_id=?""",
                (status, now, reviewed_by, now, dismissed_reason, finding_id),
            )
            session_row = self._conn.execute(
                "SELECT session_id FROM agent_findings WHERE finding_id=?", (finding_id,)
            ).fetchone()
            if session_row:
                self._conn.execute(
                    "UPDATE audit_sessions SET certified_findings = certified_findings + 1 WHERE session_id=?",
                    (session_row["session_id"],),
                )
        else:
            self._conn.execute(
                """UPDATE agent_findings
                   SET status=?, reviewed_at=?, reviewed_by=?, dismissed_reason=?
                   WHERE finding_id=?""",
                (status, now, reviewed_by, dismissed_reason, finding_id),
            )
        if status in ("reviewed", "certified", "dismissed"):
            session_row = self._conn.execute(
                "SELECT session_id FROM agent_findings WHERE finding_id=?", (finding_id,)
            ).fetchone()
            if session_row:
                self._conn.execute(
                    "UPDATE audit_sessions SET reviewed_findings = reviewed_findings + 1 WHERE session_id=?",
                    (session_row["session_id"],),
                )

    def _row_to_finding(self, row: sqlite3.Row) -> AgentFinding:
        return AgentFinding(
            finding_id=row["finding_id"],
            session_id=row["session_id"],
            task_id=row["task_id"],
            source_agent=row["source_agent"],
            domain=row["domain"],
            flag=row["flag"],
            confidence=row["confidence"],
            records=json.loads(row["records_json"] or "[]"),
            notes=row["notes"] or "",
            status=row["status"],
            dismissed_reason=row["dismissed_reason"] or "",
            created_at=row["created_at"],
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"],
            certified_at=row["certified_at"],
        )

    # ── Mensajes ──────────────────────────────────────────────────────────

    def send_message(
        self,
        from_agent: str,
        to_agent: str,
        msg_type: str,
        payload: dict[str, Any],
        session_id: Optional[str] = None,
    ) -> str:
        """Envia un mensaje entre agentes. Retorna msg_id."""
        msg_id = _uid()
        self._conn.execute(
            """INSERT INTO agent_messages
               (msg_id, session_id, from_agent, to_agent, msg_type, payload, sent_at)
               VALUES (?,?,?,?,?,?,?)""",
            (msg_id, session_id, from_agent, to_agent, msg_type,
             json.dumps(payload), _now()),
        )
        return msg_id

    def read_messages(
        self,
        to_agent: str,
        session_id: Optional[str] = None,
        unread_only: bool = True,
    ) -> list[dict]:
        """Lee mensajes para un agente. Marca como leidos si unread_only=True."""
        conditions = ["(to_agent=? OR to_agent='*')"]
        params: list[Any] = [to_agent]
        if session_id:
            conditions.append("session_id=?")
            params.append(session_id)
        if unread_only:
            conditions.append("read_at IS NULL")

        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"SELECT * FROM agent_messages WHERE {where} ORDER BY sent_at ASC",
            params,
        ).fetchall()

        msgs = [dict(r) for r in rows]
        if unread_only and msgs:
            ids = [m["msg_id"] for m in msgs]
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE agent_messages SET read_at=? WHERE msg_id IN ({placeholders})",
                [_now()] + ids,
            )
        return msgs

    # ── Stats ─────────────────────────────────────────────────────────────

    def session_stats(self, session_id: str) -> dict[str, Any]:
        """Resumen estadistico de una sesion para el dashboard."""
        session = self.get_session(session_id)
        if not session:
            return {}

        flags = self._conn.execute(
            """SELECT flag, domain, COUNT(*) as cnt, AVG(confidence) as avg_conf
               FROM agent_findings WHERE session_id=? GROUP BY flag, domain
               ORDER BY cnt DESC""",
            (session_id,),
        ).fetchall()

        by_status = self._conn.execute(
            """SELECT status, COUNT(*) as cnt FROM agent_findings
               WHERE session_id=? GROUP BY status""",
            (session_id,),
        ).fetchall()

        tasks = self._conn.execute(
            """SELECT agent_type, status, COUNT(*) as cnt FROM agent_tasks
               WHERE session_id=? GROUP BY agent_type, status""",
            (session_id,),
        ).fetchall()

        return {
            "session": {
                "session_id": session.session_id,
                "status": session.status,
                "domains": session.domains,
                "started_at": session.started_at,
                "completed_at": session.completed_at,
                "total_findings": session.total_findings,
                "reviewed_findings": session.reviewed_findings,
                "certified_findings": session.certified_findings,
            },
            "flags": [dict(r) for r in flags],
            "by_status": {r["status"]: r["cnt"] for r in by_status},
            "tasks": [dict(r) for r in tasks],
        }
