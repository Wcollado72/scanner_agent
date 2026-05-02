"""
db_scanner.py  v0.6.0 — Multi-backend database scanner for audit intelligence.

Supported backends:
  sqlite    sqlite:///path/to/file.db          (built-in, no extra deps)
  mssql     mssql+pyodbc://user:pass@server/db?driver=ODBC+Driver+18+for+SQL+Server
  mysql     mysql+pymysql://user:pass@host:3306/database
  csv       csv:///path/to/file.csv            (columna header row required)

Usage:
    from scanner_agent.scanners.db_scanner import scan_db_table, DbScanConfig

    config = DbScanConfig(
        connection_string="sqlite:///tests/data/mock_cee.db",
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
        source_alias="CEE",
    )
    records = scan_db_table(config)
"""

from __future__ import annotations

import csv
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
        "mssql+pyodbc://sa:Password@localhost/AuditDB"
            "  ?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
        "mysql+pymysql://root:password@localhost:3306/audit_db"
        "csv:///C:/data/electores.csv"

    field_map maps RowRecord field names → actual column names in the table.
    Only mapped fields are extracted. Unmapped RowRecord fields stay None.
    """

    connection_string: str
    table: str                              # table name (ignored for CSV)
    field_map: dict[str, str] = field(default_factory=dict)
    batch_size: int = 500
    where_clause: str = ""                  # Optional SQL WHERE (e.g. "activo = 1")
    source_alias: str = ""                  # Human label for reports (e.g. "CEE", "CESCO")

    @property
    def source_label(self) -> str:
        return self.source_alias or self.table

    @property
    def db_type(self) -> str:
        cs = self.connection_string.lower()
        if cs.startswith("sqlite"):
            return "sqlite"
        if cs.startswith("mssql"):
            return "mssql"
        if cs.startswith("mysql"):
            return "mysql"
        if cs.startswith("csv"):
            return "csv"
        return cs.split("://")[0] if "://" in cs else "unknown"


# ── Normalization helpers ──────────────────────────────────────────────────

def _normalize_text(text: str | None) -> str | None:
    if text is None:
        return None
    nfkd = unicodedata.normalize("NFKD", text.strip().upper())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _hash_row(record: RowRecord) -> str:
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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    safe_cols  = ", ".join(f'"{c}"' for c in columns)
    safe_table = f'"{table}"'
    where_sql  = f"WHERE {where}" if where else ""
    query = f"SELECT {safe_cols} FROM {safe_table} {where_sql}"
    logger.debug("SQLite query: %s", query)
    try:
        cur.execute(query)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            yield [dict(r) for r in rows]
    finally:
        conn.close()


# ── SQLAlchemy connector (SQL Server + MySQL) ──────────────────────────────

def _iter_sqlalchemy_batches(
    connection_string: str,
    table: str,
    columns: list[str],
    where: str,
    batch_size: int,
) -> Iterator[list[dict[str, Any]]]:
    """
    Generic SQLAlchemy batch iterator for SQL Server and MySQL.
    Requires: pip install sqlalchemy pyodbc  (SQL Server)
              pip install sqlalchemy pymysql (MySQL)
    """
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        raise ImportError(
            "SQLAlchemy is required for SQL Server / MySQL connections.\n"
            "Install with: pip install sqlalchemy"
        )

    engine = create_engine(connection_string, echo=False)
    safe_cols  = ", ".join(f'[{c}]' if "mssql" in connection_string else f'`{c}`'
                           for c in columns)
    where_sql  = f"WHERE {where}" if where else ""
    query_str  = f"SELECT {safe_cols} FROM {table} {where_sql}"
    logger.debug("SQLAlchemy query: %s", query_str)

    with engine.connect() as conn:
        result = conn.execute(text(query_str))
        keys   = list(result.keys())
        while True:
            rows = result.fetchmany(batch_size)
            if not rows:
                break
            yield [dict(zip(keys, row)) for row in rows]

    engine.dispose()


# ── CSV connector ──────────────────────────────────────────────────────────

def _iter_csv_batches(
    csv_path: str,
    columns: list[str],
    batch_size: int,
) -> Iterator[list[dict[str, Any]]]:
    """
    Read a CSV file row by row, yielding batches.
    Columns not present in the CSV are returned as None.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        batch: list[dict[str, Any]] = []
        for row in reader:
            filtered = {c: row.get(c) for c in columns}
            batch.append(filtered)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


# ── Row mapper ────────────────────────────────────────────────────────────

def _raw_to_row_record(raw: dict[str, Any], config: DbScanConfig) -> RowRecord:
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
    ap     = get("apellido_paterno") or ""
    am     = get("apellido_materno") or ""

    record = RowRecord(
        row_id       = str(get("row_id") or ""),
        source_table = config.table,
        source_db    = config.source_label,
        nombre       = nombre or None,
        apellido_paterno = ap or None,
        apellido_materno = am or None,
        fecha_nacimiento = get("fecha_nacimiento"),
        edad         = edad,
        genero       = get("genero"),
        direccion    = get("direccion"),
        municipio    = get("municipio"),
        seguro_social = get("seguro_social"),
        telefono     = get("telefono"),
        tipo_telefono = get("tipo_telefono"),
        raw_data     = dict(raw),
    )

    nombre_norm = " ".join(filter(None, [
        _normalize_text(record.nombre),
        _normalize_text(record.apellido_paterno),
        _normalize_text(record.apellido_materno),
    ]))
    object.__setattr__(record, "nombre_normalizado", nombre_norm or None)
    object.__setattr__(record, "row_hash", _hash_row(record))
    return record


# ── Main scan function ─────────────────────────────────────────────────────

def scan_db_table(config: DbScanConfig) -> list[RowRecord]:
    """
    Scan a database table or CSV and return a list of RowRecord objects.

    Supported: sqlite, mssql+pyodbc, mysql+pymysql, csv.
    """
    db_type = config.db_type
    logger.info("Starting scan: backend=%s source=%s table=%s",
                db_type, config.source_label, config.table)

    field_map  = config.field_map
    db_columns: list[str] = list(field_map.values())
    if not db_columns:
        raise ValueError("field_map is empty — provide at least one field mapping.")

    records: list[RowRecord] = []
    total_rows = 0
    skipped    = 0

    def _process(batches):
        nonlocal total_rows, skipped
        for batch in batches:
            for raw in batch:
                total_rows += 1
                try:
                    records.append(_raw_to_row_record(raw, config))
                except Exception as exc:
                    skipped += 1
                    logger.warning("Skipping malformed row #%d: %s", total_rows, exc)

    try:
        if db_type == "sqlite":
            raw_path = config.connection_string.split("sqlite:///", 1)[-1] or ":memory:"
            _process(_iter_sqlite_batches(
                raw_path, config.table, db_columns,
                config.where_clause, config.batch_size,
            ))

        elif db_type == "csv":
            csv_path = config.connection_string.split("csv:///", 1)[-1]
            _process(_iter_csv_batches(csv_path, db_columns, config.batch_size))

        elif db_type in ("mssql", "mysql"):
            _process(_iter_sqlalchemy_batches(
                config.connection_string, config.table, db_columns,
                config.where_clause, config.batch_size,
            ))

        else:
            raise NotImplementedError(
                f"Backend '{db_type}' is not supported.\n"
                f"Supported: sqlite, mssql+pyodbc, mysql+pymysql, csv"
            )

    except (sqlite3.OperationalError, Exception) as exc:
        logger.error("Scan failed [%s / %s]: %s", config.source_label, config.table, exc)
        raise

    logger.info(
        "Scan complete: backend=%s source=%s rows_read=%d skipped=%d records=%d",
        db_type, config.source_label, total_rows, skipped, len(records),
    )
    return records


# ── Connection string helpers ──────────────────────────────────────────────

def mssql_connection_string(
    server: str,
    database: str,
    username: str = "",
    password: str = "",
    driver: str = "ODBC Driver 18 for SQL Server",
    trusted: bool = False,
    trust_cert: bool = True,
) -> str:
    """
    Build a SQL Server connection string for SQLAlchemy.

    Examples:
        # Windows Authentication (trusted connection)
        mssql_connection_string("localhost", "AuditDB", trusted=True)

        # SQL Server Authentication
        mssql_connection_string("localhost\\SQLEXPRESS", "AuditDB", "sa", "pass")
    """
    import urllib.parse
    driver_enc = urllib.parse.quote_plus(driver)
    trust      = "yes" if trust_cert else "no"

    if trusted:
        return (
            f"mssql+pyodbc:///?odbc_connect="
            + urllib.parse.quote_plus(
                f"Driver={{{driver}}};Server={server};Database={database};"
                f"Trusted_Connection=yes;TrustServerCertificate={trust};"
            )
        )
    return (
        f"mssql+pyodbc:///?odbc_connect="
        + urllib.parse.quote_plus(
            f"Driver={{{driver}}};Server={server};Database={database};"
            f"UID={username};PWD={password};TrustServerCertificate={trust};"
        )
    )


def mysql_connection_string(
    host: str,
    database: str,
    username: str,
    password: str,
    port: int = 3306,
) -> str:
    """Build a MySQL connection string for SQLAlchemy."""
    return f"mysql+pymysql://{username}:{password}@{host}:{port}/{database}"
