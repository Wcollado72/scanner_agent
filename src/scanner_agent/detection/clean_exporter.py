"""
scanner_agent.detection.clean_exporter  v0.8.0

Exporta registros "limpios" (sin flags) de una fase de escaneo a una base
de datos de staging para la siguiente fase del pipeline de depuración.

Flujo del pipeline:
  Fase 1 → scan RD     → export_clean_records() → rd_cee_depurado.db
  Fase 2 → scan CEE    → export_clean_records() → rd_cee_depurado.db
  Fase 3 → cross RD+CEE (desde staging) → reporte
  Fase 4 → cross staging × CESCO → reporte final
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scanner_agent.models import RowRecord

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_flagged_ids(findings: list[dict]) -> set[str]:
    """
    Devuelve el conjunto de row_id que aparecen en algún hallazgo.
    Estos registros NO se exportan automáticamente — requieren revisión humana.

    Parámetros:
        findings : Lista de dicts de hallazgos (del reporte JSON).
    """
    flagged: set[str] = set()
    for f in findings:
        for r in f.get("records", []):
            uid = str(r.get("row_id") or r.get("num_elector") or "").strip()
            if uid:
                flagged.add(uid)
    return flagged


# ── Staging schema ─────────────────────────────────────────────────────────

_STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS registros_staging (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identidad
    row_id              TEXT,               -- ID original en la fuente
    nombre              TEXT NOT NULL,
    apellido_paterno    TEXT NOT NULL,
    apellido_materno    TEXT,
    nombre_completo     TEXT,
    fecha_nacimiento    TEXT,
    edad                INTEGER,
    genero              TEXT,

    -- Contacto
    direccion           TEXT,
    municipio           TEXT,
    telefono            TEXT,
    tipo_telefono       TEXT,
    seguro_social       TEXT,

    -- Trazabilidad
    source_db           TEXT NOT NULL,      -- CEE | RD | CESCO | STAGING
    source_table        TEXT,
    pipeline_phase      INTEGER NOT NULL,   -- 1=RD, 2=CEE, 3=cross_rdcee, 4=cesco_final
    fase_label          TEXT,
    exportado_en        TEXT DEFAULT (datetime('now')),

    -- Índices únicos para evitar duplicados entre fases
    UNIQUE (row_id, source_db)
);

CREATE INDEX IF NOT EXISTS idx_stg_ssn    ON registros_staging(seguro_social);
CREATE INDEX IF NOT EXISTS idx_stg_nombre ON registros_staging(nombre, apellido_paterno);
CREATE INDEX IF NOT EXISTS idx_stg_dob    ON registros_staging(fecha_nacimiento);
CREATE INDEX IF NOT EXISTS idx_stg_source ON registros_staging(source_db, pipeline_phase);

CREATE TABLE IF NOT EXISTS staging_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _ensure_staging_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_STAGING_SCHEMA)
    conn.execute("INSERT OR IGNORE INTO staging_meta VALUES (?,?)",
                 ("version", "0.8.0"))
    conn.execute("INSERT OR IGNORE INTO staging_meta VALUES (?,?)",
                 ("created_at", _now_iso()))
    conn.commit()


# ── Core export ────────────────────────────────────────────────────────────

def export_clean_records(
    records: list[RowRecord],
    flagged_ids: set[str],
    dest_db_path: Path,
    source_alias: str,
    phase: int,
    fase_label: str = "",
) -> dict:
    """
    Escribe a dest_db_path los registros que NO están en flagged_ids.

    Parámetros:
        records       : Lista completa de RowRecord escaneados en esta fase.
        flagged_ids   : Set de row_id con hallazgos — estos se omiten.
        dest_db_path  : Ruta a la base de staging (ej. rd_cee_depurado.db).
        source_alias  : "RD", "CEE", etc.
        phase         : Número de fase (1–4).
        fase_label    : Descripción legible de la fase.

    Retorna:
        dict con total, exported, skipped_flagged, skipped_duplicate, timestamp.
    """
    dest_db_path = Path(dest_db_path)
    dest_db_path.parent.mkdir(parents=True, exist_ok=True)

    label = fase_label or f"Fase {phase} — {source_alias}"
    now   = _now_iso()
    exported   = 0
    skip_flag  = 0
    skip_dup   = 0

    conn = sqlite3.connect(str(dest_db_path))
    try:
        _ensure_staging_schema(conn)
        cur = conn.cursor()

        for rec in records:
            uid = str(rec.row_id or "").strip()

            # Omitir registros con flags — requieren revisión humana
            if uid and uid in flagged_ids:
                skip_flag += 1
                continue

            nombre_completo = rec.nombre_completo or " ".join(
                filter(None, [rec.nombre, rec.apellido_paterno, rec.apellido_materno])
            )

            try:
                cur.execute("""
                    INSERT OR IGNORE INTO registros_staging
                        (row_id, nombre, apellido_paterno, apellido_materno,
                         nombre_completo, fecha_nacimiento, edad, genero,
                         direccion, municipio, telefono, tipo_telefono,
                         seguro_social, source_db, source_table,
                         pipeline_phase, fase_label, exportado_en)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    uid,
                    rec.nombre or "",
                    rec.apellido_paterno or "",
                    rec.apellido_materno,
                    nombre_completo,
                    rec.fecha_nacimiento,
                    rec.edad,
                    rec.genero,
                    rec.direccion,
                    rec.municipio,
                    rec.telefono,
                    rec.tipo_telefono,
                    rec.seguro_social,
                    (rec.source_db or source_alias).upper(),
                    rec.source_table,
                    phase,
                    label,
                    now,
                ))
                if cur.rowcount == 0:
                    skip_dup += 1   # UNIQUE constraint — ya existe en staging
                else:
                    exported += 1
            except sqlite3.IntegrityError:
                skip_dup += 1

        conn.commit()
        logger.info(
            "clean_export phase=%d source=%s | exported=%d flagged=%d dup=%d",
            phase, source_alias, exported, skip_flag, skip_dup,
        )
        return {
            "total":             len(records),
            "exported":          exported,
            "skipped_flagged":   skip_flag,
            "skipped_duplicate": skip_dup,
            "timestamp":         now,
        }
    except Exception:
        conn.rollback()
        logger.exception("Error en export_clean_records phase=%d", phase)
        raise
    finally:
        conn.close()


# ── Staging → RowRecord loader ─────────────────────────────────────────────

def load_staging_records(
    staging_db_path: Path,
    source_filter: str | None = None,
) -> list[RowRecord]:
    """
    Carga registros del staging como RowRecord para la siguiente fase.

    Parámetros:
        staging_db_path : Ruta a rd_cee_depurado.db.
        source_filter   : Si se provee ("RD", "CEE"), filtra por source_db.
                          None carga todos.

    Retorna:
        Lista de RowRecord con source_db preservado.
    """
    staging_db_path = Path(staging_db_path)
    if not staging_db_path.exists():
        logger.warning("Staging DB no encontrada: %s", staging_db_path)
        return []

    conn = sqlite3.connect(str(staging_db_path))
    conn.row_factory = sqlite3.Row
    try:
        if source_filter:
            rows = conn.execute("""
                SELECT * FROM registros_staging
                WHERE source_db = ?
                ORDER BY id
            """, (source_filter.upper(),)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM registros_staging ORDER BY id
            """).fetchall()

        records: list[RowRecord] = []
        for r in rows:
            d = dict(r)
            try:
                rec = RowRecord(
                    row_id           = d.get("row_id") or str(d["id"]),
                    source_table     = d.get("source_table") or "registros_staging",
                    source_db        = d.get("source_db", "STAGING"),
                    nombre           = d.get("nombre"),
                    apellido_paterno = d.get("apellido_paterno"),
                    apellido_materno = d.get("apellido_materno"),
                    fecha_nacimiento = d.get("fecha_nacimiento"),
                    edad             = d.get("edad"),
                    genero           = d.get("genero"),
                    direccion        = d.get("direccion"),
                    municipio        = d.get("municipio"),
                    seguro_social    = d.get("seguro_social"),
                    telefono         = d.get("telefono"),
                    tipo_telefono    = d.get("tipo_telefono"),
                )
                records.append(rec)
            except Exception as exc:
                logger.warning("Omitiendo registro staging id=%s: %s", d.get("id"), exc)

        logger.info(
            "Staging cargado: %d registros | fuente=%s | %s",
            len(records), source_filter or "ALL", staging_db_path,
        )
        return records
    finally:
        conn.close()


def get_staging_stats(staging_db_path: Path) -> dict:
    """Estadísticas resumidas del staging DB."""
    staging_db_path = Path(staging_db_path)
    if not staging_db_path.exists():
        return {"total": 0, "por_fuente": {}, "por_fase": {}}

    conn = sqlite3.connect(str(staging_db_path))
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM registros_staging"
        ).fetchone()[0]

        por_fuente = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT source_db, COUNT(*) FROM registros_staging GROUP BY source_db"
            ).fetchall()
        }
        por_fase = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT pipeline_phase, COUNT(*) FROM registros_staging GROUP BY pipeline_phase"
            ).fetchall()
        }
        return {"total": total, "por_fuente": por_fuente, "por_fase": por_fase}
    finally:
        conn.close()
