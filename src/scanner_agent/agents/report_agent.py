"""
scanner_agent.agents.report_agent  v1.0.0

ReportAgent — agente de certificacion y exportacion de resultados.

Responsabilidad:
    1. Tomar findings en estado 'reviewed' o 'certified' del AgentBus
    2. Escribir a la matriz_certificada (SQLite) los hallazgos aprobados
    3. Generar un reporte JSON de la sesion completa
    4. Generar un resumen ejecutivo (texto plano) para el auditor
    5. Actualizar el execution_log con el resultado final

Este agente es el ultimo en el pipeline.
Solo lee findings ya validados por el ReviewAgent.

Payload esperado en AgentTask:
    {
        "session_id": "...",
        "output_dir": "reports/",           # directorio de salida
        "matriz_path": "tests/data/matriz_certificada.db",
        "include_dismissed": false,          # incluir dismissed en reporte
        "formats": ["json", "summary"]       # formatos de exportacion
    }
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scanner_agent.agents.base_agent import BaseAgent, AgentConfig
from scanner_agent.agents.bus import AgentBus, AgentTask

logger = logging.getLogger(__name__)

# Esquema de la tabla de reporte en matriz_certificada
_FINDINGS_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_certified_findings (
    finding_id      TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    source_agent    TEXT NOT NULL,
    domain          TEXT NOT NULL,
    flag            TEXT NOT NULL,
    confidence      REAL NOT NULL,
    record_count    INTEGER DEFAULT 0,
    records_json    TEXT DEFAULT '[]',
    notes           TEXT DEFAULT '',
    status          TEXT NOT NULL,
    dismissed_reason TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    certified_at    TEXT,
    certified_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_acf_session ON agent_certified_findings(session_id);
CREATE INDEX IF NOT EXISTS idx_acf_flag    ON agent_certified_findings(flag);
CREATE INDEX IF NOT EXISTS idx_acf_domain  ON agent_certified_findings(domain);
"""


class ReportAgent(BaseAgent):
    """
    Agente de reportes y certificacion final.

    Consolida los findings validados, los persiste en la matriz
    certificada y genera los archivos de reporte.
    """

    @property
    def agent_type(self) -> str:
        return "report"

    def run(self, task: AgentTask) -> dict[str, Any]:
        payload    = task.payload
        session_id = payload.get("session_id") or task.session_id
        output_dir = Path(payload.get("output_dir", "reports"))
        matriz_path = payload.get("matriz_path", str(output_dir / "matriz_certificada.db"))
        include_dismissed = payload.get("include_dismissed", False)
        formats    = payload.get("formats", ["json", "summary"])

        if not self.config.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info(
            "ReportAgent iniciando sesion=%s output=%s",
            session_id[:8], output_dir,
        )

        # 1. Cargar findings validados
        statuses = ["certified", "reviewed"]
        if include_dismissed:
            statuses.append("dismissed")

        all_findings = []
        for status in statuses:
            all_findings.extend(
                self.bus.get_findings(session_id=session_id, status=status)
            )

        certified = [f for f in all_findings if f.status == "certified"]
        reviewed  = [f for f in all_findings if f.status == "reviewed"]
        dismissed = [f for f in all_findings if f.status == "dismissed"]

        self.logger.info(
            "Findings: certified=%d reviewed=%d dismissed=%d",
            len(certified), len(reviewed), len(dismissed),
        )

        # 2. Escribir a matriz_certificada
        if not self.config.dry_run:
            written = self._write_to_matriz(
                certified + reviewed, matriz_path, session_id
            )
            self.logger.info("Escritos a matriz_certificada: %d", written)
        else:
            written = len(certified) + len(reviewed)
            self.logger.info("[DRY-RUN] Se escribirian %d findings", written)

        # 3. Generar reporte JSON
        report_path = None
        if "json" in formats and not self.config.dry_run:
            report_path = self._write_json_report(
                session_id=session_id,
                certified=certified,
                reviewed=reviewed,
                dismissed=dismissed,
                output_dir=output_dir,
            )

        # 4. Generar resumen ejecutivo
        summary_path = None
        if "summary" in formats and not self.config.dry_run:
            summary_path = self._write_summary(
                session_id=session_id,
                certified=certified,
                reviewed=reviewed,
                dismissed=dismissed,
                output_dir=output_dir,
            )

        result = {
            "session_id": session_id,
            "certified": len(certified),
            "reviewed": len(reviewed),
            "dismissed": len(dismissed),
            "written_to_matriz": written,
            "report_path": str(report_path) if report_path else "",
            "summary_path": str(summary_path) if summary_path else "",
        }

        self.logger.info(
            "ReportAgent completado: %d certificados, %d en revision",
            len(certified), len(reviewed),
        )
        return result

    # ── Escritura a matriz_certificada ────────────────────────────────────

    def _write_to_matriz(
        self, findings: list, matriz_path: str, session_id: str
    ) -> int:
        """Escribe findings a la tabla agent_certified_findings en matriz_certificada."""
        Path(matriz_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(matriz_path)
        conn.executescript(_FINDINGS_TABLE_SCHEMA)

        count = 0
        for f in findings:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO agent_certified_findings
                       (finding_id, session_id, source_agent, domain, flag,
                        confidence, record_count, records_json, notes, status,
                        dismissed_reason, created_at, certified_at, certified_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f.finding_id,
                        session_id,
                        f.source_agent,
                        f.domain,
                        f.flag,
                        f.confidence,
                        f.record_count,
                        json.dumps(f.records),
                        f.notes,
                        f.status,
                        f.dismissed_reason,
                        f.created_at,
                        f.certified_at or datetime.now(timezone.utc).isoformat(),
                        self.agent_name,
                    ),
                )
                count += 1
            except Exception as exc:
                self.logger.warning("Error escribiendo finding %s: %s", f.finding_id[:8], exc)

        conn.commit()
        conn.close()
        return count

    # ── Reporte JSON ──────────────────────────────────────────────────────

    def _write_json_report(
        self,
        session_id: str,
        certified: list,
        reviewed: list,
        dismissed: list,
        output_dir: Path,
    ) -> Path:
        """Genera un reporte JSON completo de la sesion."""
        stats = self.bus.session_stats(session_id)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"report_{session_id[:8]}_{ts}.json"

        def _finding_to_dict(f) -> dict:
            return {
                "finding_id": f.finding_id,
                "source_agent": f.source_agent,
                "domain": f.domain,
                "flag": f.flag,
                "confidence": round(f.confidence, 4),
                "record_count": f.record_count,
                "notes": f.notes,
                "status": f.status,
                "dismissed_reason": f.dismissed_reason,
                "created_at": f.created_at,
            }

        report = {
            "report_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "summary": {
                "certified": len(certified),
                "reviewed_pending": len(reviewed),
                "dismissed": len(dismissed),
                "total": len(certified) + len(reviewed) + len(dismissed),
            },
            "session_stats": stats,
            "certified_findings": [_finding_to_dict(f) for f in certified],
            "reviewed_findings":  [_finding_to_dict(f) for f in reviewed],
            "dismissed_findings": [_finding_to_dict(f) for f in dismissed],
        }

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        self.logger.info("Reporte JSON: %s", path)
        return path

    # ── Resumen ejecutivo ─────────────────────────────────────────────────

    def _write_summary(
        self,
        session_id: str,
        certified: list,
        reviewed: list,
        dismissed: list,
        output_dir: Path,
    ) -> Path:
        """Genera un resumen ejecutivo en texto plano para el auditor."""
        session = self.bus.get_session(session_id)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"summary_{session_id[:8]}_{ts}.txt"

        # Agrupar por dominio y flag
        from collections import Counter
        cert_by_flag   = Counter(f.flag for f in certified)
        review_by_flag = Counter(f.flag for f in reviewed)
        cert_by_domain = Counter(f.domain for f in certified)

        lines = [
            "=" * 65,
            "  SCANNER AGENT — RESUMEN EJECUTIVO DE AUDITORIA",
            "=" * 65,
            f"  Sesion:     {session_id}",
            f"  Generado:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"  Dominios:   {', '.join(session.domains) if session else 'N/A'}",
            f"  Estado:     {session.status if session else 'N/A'}",
            "",
            "  HALLAZGOS CERTIFICADOS",
            "  " + "-" * 40,
        ]

        if certified:
            for flag, cnt in cert_by_flag.most_common():
                lines.append(f"    {flag:<35} {cnt:>4}")
            lines.append(f"    {'TOTAL':<35} {len(certified):>4}")
        else:
            lines.append("    (ninguno)")

        lines += [
            "",
            "  HALLAZGOS EN REVISION (requieren auditor humano)",
            "  " + "-" * 40,
        ]

        if reviewed:
            for flag, cnt in review_by_flag.most_common():
                lines.append(f"    {flag:<35} {cnt:>4}")
            lines.append(f"    {'TOTAL':<35} {len(reviewed):>4}")
        else:
            lines.append("    (ninguno)")

        lines += [
            "",
            "  POR DOMINIO (certificados)",
            "  " + "-" * 40,
        ]
        for domain, cnt in cert_by_domain.most_common():
            lines.append(f"    {domain:<35} {cnt:>4}")

        lines += [
            "",
            f"  Descartados (dismissed):    {len(dismissed)}",
            "=" * 65,
            "",
        ]

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))

        self.logger.info("Resumen ejecutivo: %s", path)
        return path
