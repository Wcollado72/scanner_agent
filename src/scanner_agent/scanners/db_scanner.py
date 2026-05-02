"""
db_scanner.py — Database scanner for audit intelligence.

Phase 2: SQLite support (local, zero-dependency testing).
Designed to be extended to SQL Server, PostgreSQL, Oracle, or CSV
when real database access is granted.

Usage:
    from scanner_agent.scanners.db_scanner import scan_db_table, DbScanConfig

    config = DbScanConfig(
        connection_string="sqlite:///path/to/cee.db",
        table="electores",
        field_map={
            "row_id":           "num_elector",
            "nombre":           "nombre",
            "apellido_paterno": "apellido_paterno",
            "apellido_materno": "apellido_materno",
            "fecha_nacimiento": "fecha_nacimiento",
            "edad":             "edad",
            "genero":           "genero",
            "direccion":        "direccion",
            "municipio":        "municipio",
            "seguro_social":    "seguro_social",
        },
        batch_size=500,
    )
    records = scan_db_table(config)
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterator

from scanner_agent.models import RowRecord

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DbScanConfig:
    """
    Configuration for a single database table scan.

    connection_string examples:
        "sqlite:///C:/scanner_agent/tests/data/mock_cee.db"
        "sqlite:///:memory:"
        "mssql://server/database"   # Phase 3 — not yet implemented
        "postgresql://host/db"      # Phase 3 — not yet implemented

    field_map maps RowRecord field names → actual column names in the table.
    Only mapped fields are extracted. Unmapped RowRecord fields stay None.
    """

    connection_string: str
    table: str
    field_map: dict[str, str] = field(default_factory=dict)
    batch_size: int = 500
    where_clause: str = ""          # Optional SQL WHERE (e.g. "activo = 1")
    source_alias: str = ""          # Human label for reports (e.g. "CEE", "CESCO")

    @property
    def source_label(self) -> str:
        return self.source_alias or self.table

    @property
    def db_type(self) -> str:
        prefix = self.connection_string.split("://")[0].lower()
        return prefix if "://" in self.connection_string else "sqlite"


# ── Normalization helpers ──────────────────────────────────────────────────

def _normalize_text(text: str | None) -> str | None:
    """Uppercase, strip, remove diacritics — used for canonical matching."""
    if text is None:
        return None
    nfkd = unicodedata.normalize("NFKD", text.strip().upper())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _hash_row(record: RowRecord) -> str:
    """
    Compute a SHA-256 hash of the canonical identity fields.
    Two records with identical hash are exact duplicates.
    """
    parts = [
        _normalize_text(record.nombre) or "",
        _normalize_text(record.apellido_paterno) or "",
        _normalize_text(record.apellido_materno) or "",
        record.fecha_nacimiento or "",
        record.seguro_social or "",
    ]
    key = "|".join(parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ── SQLite connector ───────────────────────────────────────────────────────

def _iter_sqlite_batches(
    db_path: str,
    table: str,
    columns: list[str],
    where: str,
    batch_size: int,
) -> Iterator[list[dict[str, Any]]]:
    """Yield batches of rows from a SQLite table without loading everything at once."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    safe_cols = ", ".join(f'"{c}"' for c in columns)
    safe_table = f'"{table}"'
    where_sql = f"WHERE {where}" if where else ""
    query = f"SELECT {safe_cols} FROM {safe_table} {where_sql}"

    logger.debug("DB query: %s", query)

    try:
        cur.execute(query)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            yield [dict(r) for r in rows]
    finally:
        conn.close()


# ── Main scan function ─────────────────────────────────────────────────────

def scan_db_table(config: DbScanConfig) -> list[RowRecord]:
    """
    Scan a single database table and return a list of RowRecord objects.

    Currently supports SQLite (connection_string starting with "sqlite:").
    Phase 3 will add SQL Server, PostgreSQL, CSV connectors.
    """
    db_type = config.db_type

    if db_type not in ("sqlite",):
        raise NotImplementedError(
            f"Database type '{db_type}' is not yet supported. "
            f"Supported: sqlite. SQL Server / PostgreSQL planned for Phase 3."
        )

    logger.info("Starting DB scan: table=%s source=%s", config.table, config.source_label)

    # Extract the file path from the connection string
    # "sqlite:///path/to/file.db" → "/path/to/file.db"
    # "sqlite:///:memory:"        → ":memory:"
    raw_path = config.connection_string.split("sqlite:///", 1)[-1]
    if not raw_path:
        raw_path = ":memory:"

    # Determine which columns to fetch
    field_map = config.field_map
    db_columns: list[str] = list(field_map.values())

    if not db_columns:
        raise ValueError(
            "field_map is empty. Provide at least one mapping from RowRecord field → column name."
        )

    records: list[RowRecord] = []
    total_rows = 0
    skipped = 0

    try:
        for batch in _iter_sqlite_batches(
            db_path=raw_path,
            table=config.table,
            columns=db_columns,
            where=config.where_clause,
            batch_size=config.batch_size,
        ):
            for raw in batch:
                total_rows += 1
                try:
                    record = _raw_to_row_record(raw, config)
                    records.append(record)
                except Exception as exc:
                    skipped += 1
                    logger.warning("Skipping malformed row: %s", exc)

    except sqlite3.OperationalError as exc:
        logger.error("DB scan failed for table '%s': %s", config.table, exc)
        raise

    logger.info(
        "DB scan complete: table=%s rows_read=%d skipped=%d records=%d",
        config.table, total_rows, skipped, len(records),
    )
    return records


def _raw_to_row_record(raw: dict[str, Any], config: DbScanConfig) -> RowRecord:
    """Map a raw database row dict to a RowRecord using the field_map."""
    fmap = config.field_map

    def get(field_name: str) -> Any:
        col = fmap.get(field_name)
        return raw.get(col) if col else None

    edad_val = get("edad")
    try:
        edad = int(edad_val) if edad_val is not None else None
    except (ValueError, TypeError):
        edad = None

    nombre = get("nombre") or ""
    ap = get("apellido_paterno") or ""
    am = get("apellido_materno") or ""

    record = RowRecord(
        row_id=str(get("row_id") or ""),
        source_table=config.table,
        source_db=config.source_label,
        nombre=nombre or None,
        apellido_paterno=ap or None,
        apellido_materno=am or None,
        fecha_nacimiento=get("fecha_nacimiento"),
        edad=edad,
        genero=get("genero"),
        direccion=get("direccion"),
        municipio=get("municipio"),
        seguro_social=get("seguro_social"),
        raw_data=dict(raw),
    )

    # Compute normalization and hash
    nombre_norm = " ".join(filter(None, [
        _normalize_text(record.nombre),
        _normalize_text(record.apellido_paterno),
        _normalize_text(record.apellido_materno),
    ]))
    object.__setattr__(record, "nombre_normalizado", nombre_norm or None)
    object.__setattr__(record, "row_hash", _hash_row(record))

    return record
