"""
scanner_agent.execution_plan  v0.9.0

Flujo Plan -> Aprobar -> Ejecutar para todas las operaciones de auditoria.

Uso en CLI:
    plan = build_pipeline_plan(sources, staging_path, output_dir)
    display_plan(plan)
    if not confirm_execution(plan, require_confirmation=True):
        sys.exit(0)
    log_execution_start(plan, approved_by="CLI")

Uso en web:
    plan = build_pipeline_plan(...)
    plan_dict = plan.to_dict()   # serializable a JSON para el template
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Modelo del plan ────────────────────────────────────────────────────────

@dataclass
class PlannedOperation:
    """Una operacion atomica dentro del plan de ejecucion."""
    phase:        int           # 1-N
    label:        str           # "Depuracion interna RD"
    source:       str           # "RD", "CEE", "NOMINA"
    detector:     str           # "record_matcher", "nomina_scanner", "cross_db"
    table:        str           # tabla a escanear
    estimated_rows: int = 0     # 0 si no disponible
    output_file:  str = ""      # nombre del JSON de reporte
    flags:        list[str] = field(default_factory=list)  # flags que se detectaran


@dataclass
class ExecutionPlan:
    """Plan completo de ejecucion antes de correr cualquier operacion."""
    scanner:      str           # "pipeline", "nomina", "cross-db"
    operations:   list[PlannedOperation] = field(default_factory=list)
    sources:      dict[str, str] = field(default_factory=dict)  # alias -> conn_string
    output_dir:   str = ""
    staging_path: str = ""
    created_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings:     list[str] = field(default_factory=list)

    @property
    def total_phases(self) -> int:
        return len(self.operations)

    @property
    def total_estimated_rows(self) -> int:
        return sum(op.estimated_rows for op in self.operations)

    @property
    def all_flags(self) -> list[str]:
        seen = []
        for op in self.operations:
            for f in op.flags:
                if f not in seen:
                    seen.append(f)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner":      self.scanner,
            "created_at":   self.created_at,
            "output_dir":   self.output_dir,
            "staging_path": self.staging_path,
            "sources":      self.sources,
            "warnings":     self.warnings,
            "total_phases": self.total_phases,
            "total_estimated_rows": self.total_estimated_rows,
            "all_flags":    self.all_flags,
            "operations": [
                {
                    "phase":          op.phase,
                    "label":          op.label,
                    "source":         op.source,
                    "detector":       op.detector,
                    "table":          op.table,
                    "estimated_rows": op.estimated_rows,
                    "output_file":    op.output_file,
                    "flags":          op.flags,
                }
                for op in self.operations
            ],
        }


# ── Constructores de planes ────────────────────────────────────────────────

def _count_rows(conn_str: str, table: str) -> int:
    """Cuenta registros en una tabla SQLite. Retorna 0 si falla."""
    try:
        if not conn_str.startswith("sqlite:///"):
            return 0
        path = conn_str.split("sqlite:///", 1)[-1]
        if not Path(path).exists():
            return 0
        conn = sqlite3.connect(path)
        n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def build_pipeline_plan(
    source_map: dict[str, tuple[str, str]],   # alias -> (conn_str, table)
    staging_path: Path,
    output_dir: Path,
) -> ExecutionPlan:
    """Construye el plan para --scanner pipeline (4 fases electorales)."""
    from scanner_agent.models import AuditFlag

    plan = ExecutionPlan(
        scanner     = "pipeline",
        output_dir  = str(output_dir),
        staging_path= str(staging_path),
        sources     = {alias: f"{conn}:{tbl}"
                       for alias, (conn, tbl) in source_map.items()},
    )

    # Validar que las fuentes existan
    for alias, (conn, tbl) in source_map.items():
        if conn.startswith("sqlite:///"):
            path = conn.split("sqlite:///", 1)[-1]
            if not Path(path).exists():
                plan.warnings.append(
                    f"ADVERTENCIA: {alias} — archivo no encontrado: {path}"
                )

    # Fase 1 — RD interno
    if "RD" in source_map:
        conn, tbl = source_map["RD"]
        plan.operations.append(PlannedOperation(
            phase=1, label="Depuracion interna Registro Demografico",
            source="RD", detector="record_matcher", table=tbl,
            estimated_rows=_count_rows(conn, tbl),
            output_file="pipeline_fase1_rd.json",
            flags=[AuditFlag.EXACT_DUPLICATE, AuditFlag.NEAR_DUPLICATE,
                   AuditFlag.POSSIBLE_DECEASED, AuditFlag.ANOMALY],
        ))

    # Fase 2 — CEE interno
    if "CEE" in source_map:
        conn, tbl = source_map["CEE"]
        plan.operations.append(PlannedOperation(
            phase=2, label="Depuracion interna CEE padron electoral",
            source="CEE", detector="record_matcher", table=tbl,
            estimated_rows=_count_rows(conn, tbl),
            output_file="pipeline_fase2_cee.json",
            flags=[AuditFlag.EXACT_DUPLICATE, AuditFlag.NEAR_DUPLICATE,
                   AuditFlag.ADDRESS_CLUSTER, AuditFlag.POSSIBLE_DECEASED,
                   AuditFlag.ANOMALY],
        ))

    # Fase 3 — Cruce RD x CEE
    plan.operations.append(PlannedOperation(
        phase=3, label="Cruce RD x CEE (registros limpios del staging)",
        source="RD+CEE", detector="cross_db_matcher", table="registros_staging",
        estimated_rows=0,   # se conoce solo despues de fases 1+2
        output_file="pipeline_fase3_rdcee_cross.json",
        flags=[AuditFlag.CONFIRMED_DECEASED, AuditFlag.SSN_CROSS_DB_CONFLICT],
    ))

    # Fase 4 — Validacion CESCO
    if "CESCO" in source_map:
        conn, tbl = source_map["CESCO"]
        plan.operations.append(PlannedOperation(
            phase=4, label="Validacion final staging x CESCO (Real ID)",
            source="CESCO", detector="cross_db_matcher", table=tbl,
            estimated_rows=_count_rows(conn, tbl),
            output_file="pipeline_fase4_cesco_final.json",
            flags=[AuditFlag.NAME_MISMATCH_CROSS_DB, AuditFlag.SSN_CROSS_DB_CONFLICT],
        ))

    return plan


def build_nomina_plan(
    nomina_conn: str, nomina_table: str,
    rd_conn: str | None, rd_table: str | None,
    output_dir: Path,
) -> ExecutionPlan:
    """Construye el plan para --scanner nomina."""
    from scanner_agent.models import AuditFlag

    sources = {"NOMINA": f"{nomina_conn}:{nomina_table}"}
    if rd_conn:
        sources["RD"] = f"{rd_conn}:{rd_table}"

    plan = ExecutionPlan(
        scanner    = "nomina",
        output_dir = str(output_dir),
        sources    = sources,
    )

    plan.operations.append(PlannedOperation(
        phase=1, label="Auditoria interna nomina gobierno",
        source="NOMINA", detector="nomina_scanner", table=nomina_table,
        estimated_rows=_count_rows(nomina_conn, nomina_table),
        output_file="audit_nomina.json",
        flags=[
            AuditFlag.MULTI_AGENCY_EMPLOYEE,
            AuditFlag.SALARY_ANOMALY,
            *([ AuditFlag.DECEASED_EMPLOYEE] if rd_conn else []),
        ],
    ))

    return plan


# ── Display del plan ───────────────────────────────────────────────────────

def display_plan(plan: ExecutionPlan) -> None:
    """Imprime el plan de ejecucion en terminal de forma clara."""
    sep  = "═" * 66
    sep2 = "─" * 66

    print(f"\n{sep}")
    print(f"  PLAN DE EJECUCION — scanner: {plan.scanner.upper()}")
    print(f"  Generado: {plan.created_at[:19].replace('T',' ')} UTC")
    print(sep)

    print(f"\n  FUENTES DE DATOS ({len(plan.sources)}):")
    for alias, spec in plan.sources.items():
        conn = spec.rsplit(":", 1)[0] if ":" in spec else spec
        tbl  = spec.rsplit(":", 1)[-1] if ":" in spec else ""
        db_short = conn.split("///")[-1] if "///" in conn else conn
        print(f"    [{alias}]  {db_short}  →  tabla: {tbl}")

    if plan.staging_path:
        print(f"\n  STAGING DB:")
        print(f"    {plan.staging_path}")

    print(f"\n  OPERACIONES PLANIFICADAS ({plan.total_phases} fases):")
    print(f"  {sep2}")

    for op in plan.operations:
        rows_str = f"{op.estimated_rows:,} registros" if op.estimated_rows else "registros: desconocido"
        print(f"\n  Fase {op.phase}  —  {op.label}")
        print(f"    Fuente    : {op.source}  ({rows_str})")
        print(f"    Detector  : {op.detector}")
        print(f"    Detectara : {', '.join(op.flags)}")
        print(f"    Reporte   : {plan.output_dir}/{op.output_file}")

    print(f"\n  {sep2}")
    rows_total = plan.total_estimated_rows
    if rows_total:
        print(f"  Total estimado : {rows_total:,} registros a procesar")
    print(f"  Flags activos  : {len(plan.all_flags)}")
    for f in plan.all_flags:
        print(f"    • {f}")
    print(f"  Output dir     : {plan.output_dir}")

    if plan.warnings:
        print(f"\n  ⚠  ADVERTENCIAS:")
        for w in plan.warnings:
            print(f"    {w}")

    print(f"\n{sep}")


# ── Confirmacion interactiva ───────────────────────────────────────────────

def confirm_execution(
    plan: ExecutionPlan,
    require_confirmation: bool = True,
    approved_by: str = "",
) -> bool:
    """
    Muestra el plan y solicita confirmacion explicita.

    Retorna True si el usuario aprueba, False si cancela.
    Si require_confirmation=False, aprueba automaticamente (modo CI/test).
    """
    if not require_confirmation:
        logger.info("Auto-aprobado (require_confirmation=False) | scanner=%s", plan.scanner)
        return True

    if plan.warnings:
        print("\n  ⚠  Hay advertencias. Revisa antes de continuar.")

    user_label = f" [{approved_by}]" if approved_by else ""
    print(f"\n  ¿Confirmar ejecucion{user_label}? [s/N] ", end="", flush=True)

    try:
        resp = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelado.")
        return False

    if resp in ("s", "si", "sí", "y", "yes"):
        print(f"  Aprobado. Iniciando ejecucion...\n")
        return True
    else:
        print(f"  Cancelado. No se ejecuto ninguna operacion.")
        return False


# ── Registro de ejecuciones ───────────────────────────────────────────────

_EXEC_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scanner         TEXT NOT NULL,
    approved_by     TEXT NOT NULL DEFAULT 'CLI',
    approved_at     TEXT NOT NULL,
    sources         TEXT,        -- JSON
    output_dir      TEXT,
    staging_path    TEXT,
    plan_json       TEXT,        -- plan completo serializado
    status          TEXT DEFAULT 'started',  -- started | completed | failed
    completed_at    TEXT,
    total_findings  INTEGER,
    error_message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_execlog_scanner ON execution_log(scanner);
CREATE INDEX IF NOT EXISTS idx_execlog_status  ON execution_log(status);
"""


def log_execution_start(
    plan: ExecutionPlan,
    log_db_path: Path,
    approved_by: str = "CLI",
) -> int:
    """
    Registra el inicio de una ejecucion aprobada.
    Retorna el ID del registro para actualizar al finalizar.
    """
    import json
    log_db_path = Path(log_db_path)
    log_db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(log_db_path))
    conn.executescript(_EXEC_LOG_SCHEMA)
    cur = conn.execute("""
        INSERT INTO execution_log
          (scanner, approved_by, approved_at, sources, output_dir,
           staging_path, plan_json, status)
        VALUES (?,?,?,?,?,?,?,'started')
    """, (
        plan.scanner,
        approved_by,
        datetime.now(timezone.utc).isoformat(),
        json.dumps(plan.sources),
        plan.output_dir,
        plan.staging_path,
        json.dumps(plan.to_dict()),
    ))
    exec_id = cur.lastrowid
    conn.commit()
    conn.close()
    logger.info("Ejecucion registrada: id=%d scanner=%s approved_by=%s",
                exec_id, plan.scanner, approved_by)
    return exec_id


def log_execution_complete(
    log_db_path: Path,
    exec_id: int,
    total_findings: int = 0,
    status: str = "completed",
    error_message: str = "",
) -> None:
    """Actualiza el registro con el resultado de la ejecucion."""
    log_db_path = Path(log_db_path)
    if not log_db_path.exists():
        return

    conn = sqlite3.connect(str(log_db_path))
    conn.execute("""
        UPDATE execution_log
        SET status=?, completed_at=?, total_findings=?, error_message=?
        WHERE id=?
    """, (
        status,
        datetime.now(timezone.utc).isoformat(),
        total_findings,
        error_message,
        exec_id,
    ))
    conn.commit()
    conn.close()
    logger.info("Ejecucion completada: id=%d status=%s findings=%d",
                exec_id, status, total_findings)


def get_execution_history(log_db_path: Path, limit: int = 20) -> list[dict]:
    """Retorna el historial de ejecuciones para el dashboard web."""
    log_db_path = Path(log_db_path)
    if not log_db_path.exists():
        return []

    conn = sqlite3.connect(str(log_db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, scanner, approved_by, approved_at, status,
               completed_at, total_findings, output_dir, error_message
        FROM execution_log
        ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
