"""
Tests for db_scanner.py and record_matcher.py using SQLite in-memory databases.
No external dependencies — only Python stdlib + scanner_agent.
"""

import sqlite3
import tempfile

import pytest

from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
from scanner_agent.detection.record_matcher import (
    find_deceased_and_anomalies,
    find_exact_duplicates,
    find_near_duplicates,
    run_full_match,
)

# ── Helpers ────────────────────────────────────────────────────────────────

CEE_FIELD_MAP = {
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
}


def _create_test_db(rows: list[tuple]) -> str:
    """Create a temp SQLite file with the given rows. Returns sqlite:/// connection string."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE electores (
            num_elector TEXT PRIMARY KEY,
            nombre TEXT, apellido_paterno TEXT, apellido_materno TEXT,
            fecha_nacimiento TEXT, edad INTEGER, genero TEXT,
            direccion TEXT, municipio TEXT, seguro_social TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO electores VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return f"sqlite:///{tmp.name}"


def _row(num, nombre, ap, am, dob, edad, genero="M",
         dir_="Calle A #1", mun="San Juan", ssn=None):
    return (num, nombre, ap, am, dob, edad, genero, dir_, mun, ssn)


def _config(conn_str: str) -> DbScanConfig:
    return DbScanConfig(
        connection_string=conn_str,
        table="electores",
        field_map=CEE_FIELD_MAP,
    )


# ── db_scanner tests ───────────────────────────────────────────────────────

def test_scan_returns_row_records():
    """scan_db_table returns one RowRecord per table row."""
    db = _create_test_db([
        _row("CEE001", "José", "Rivera", "Cruz", "1985-06-15", 39),
        _row("CEE002", "María", "Torres", "Pérez", "1990-03-22", 34, "F"),
    ])
    records = scan_db_table(_config(db))
    assert len(records) == 2
    assert records[0].source_table == "electores"


def test_scan_fields_populated_correctly():
    """All mapped fields transfer correctly to RowRecord."""
    db = _create_test_db([
        _row("CEE001", "Carlos", "López", "Díaz", "1978-11-30", 46,
             "M", "Calle Sol #5", "Ponce", "***-**-1234"),
    ])
    r = scan_db_table(_config(db))[0]
    assert r.row_id == "CEE001"
    assert r.nombre == "Carlos"
    assert r.apellido_paterno == "López"
    assert r.fecha_nacimiento == "1978-11-30"
    assert r.edad == 46
    assert r.municipio == "Ponce"
    assert r.seguro_social == "***-**-1234"


def test_scan_computes_row_hash():
    """Each RowRecord gets a 64-char deterministic SHA-256 row_hash."""
    db = _create_test_db([
        _row("CEE001", "José", "Rivera", "Cruz", "1985-06-15", 39),
    ])
    r = scan_db_table(_config(db))[0]
    assert r.row_hash is not None
    assert len(r.row_hash) == 64


def test_scan_normalizes_name_strips_accents():
    """nombre_normalizado removes diacritics and uppercases — José → JOSE."""
    db = _create_test_db([
        _row("CEE001", "José", "Pérez", "López", "1980-01-01", 44),
    ])
    r = scan_db_table(_config(db))[0]
    assert r.nombre_normalizado is not None
    assert "JOSE" in r.nombre_normalizado
    assert "PEREZ" in r.nombre_normalizado


def test_scan_empty_table():
    """Scanning an empty table returns an empty list without error."""
    db = _create_test_db([])
    assert scan_db_table(_config(db)) == []


def test_scan_batch_processing_consistent():
    """batch_size does not change the total number of records returned."""
    rows = [_row(f"CEE{i:03d}", "Juan", "García", "Rios", "1990-01-01", 34)
            for i in range(25)]
    db = _create_test_db(rows)
    cfg_small = DbScanConfig(connection_string=db, table="electores",
                             field_map=CEE_FIELD_MAP, batch_size=5)
    cfg_large = DbScanConfig(connection_string=db, table="electores",
                             field_map=CEE_FIELD_MAP, batch_size=1000)
    assert len(scan_db_table(cfg_small)) == 25
    assert len(scan_db_table(cfg_large)) == 25


# ── record_matcher tests ───────────────────────────────────────────────────

def test_exact_duplicates_same_normalized_name():
    """
    José/Jose → normalized to JOSE → same hash → detected as EXACT_DUPLICATE.
    (Normalization correctly treats accent-only differences as exact matches.)
    """
    db = _create_test_db([
        _row("A", "José", "Rivera", "Cruz", "1985-06-15", 39, mun="Bayamón"),
        _row("B", "Jose", "Rivera", "Cruz", "1985-06-15", 39, mun="Bayamón"),
        _row("C", "Luis", "García", "Ríos", "1990-01-01", 35),
    ])
    records = scan_db_table(_config(db))
    groups = find_exact_duplicates(records)
    assert len(groups) == 1
    assert groups[0].flag == "EXACT_DUPLICATE"
    assert {r.row_id for r in groups[0].records} == {"A", "B"}


def test_near_duplicate_real_spelling_difference():
    """
    Real spelling typo (MARTA vs MART) with same DOB → NEAR_DUPLICATE.
    These do NOT normalize to the same string.
    """
    db = _create_test_db([
        _row("A", "Marta", "Pérez", "Nieves", "1932-10-08", 92, "F", mun="Caguas"),
        _row("B", "Mart",  "Pérez", "Nieves", "1932-10-08", 92, "F", mun="Caguas"),
    ])
    records = scan_db_table(_config(db))
    groups = find_near_duplicates(records)
    assert len(groups) >= 1
    assert any(g.flag == "NEAR_DUPLICATE" for g in groups)
    assert any(g.confidence >= 0.85 for g in groups)


def test_possible_deceased_detected():
    """A record with edad > 100 is flagged as POSSIBLE_DECEASED."""
    db = _create_test_db([
        _row("A", "Ramona", "Colón", "Rivera", "1918-05-10", 106, "F"),
        _row("B", "Luis",   "Ortiz",  "Ríos",  "1990-01-01",  35),
    ])
    records = scan_db_table(_config(db))
    groups = find_deceased_and_anomalies(records)
    deceased = [g for g in groups if g.flag == "POSSIBLE_DECEASED"]
    assert len(deceased) == 1
    assert deceased[0].records[0].row_id == "A"


def test_anomalous_age_future_dob():
    """A record with a DOB in the future (negative effective age) is flagged ANOMALY."""
    db = _create_test_db([
        _row("A", "Futuro", "García", "López", "2045-01-01", -19),
    ])
    records = scan_db_table(_config(db))
    groups = find_deceased_and_anomalies(records)
    assert any(g.flag == "ANOMALY" for g in groups)


def test_run_full_match_pipeline():
    """run_full_match returns exact + near-dup + deceased groups in one pass."""
    db = _create_test_db([
        # Exact pair (accent only → exact after normalization)
        _row("A", "Ana", "Soto", "Vega", "1980-07-04", 44, "F", mun="Ponce"),
        _row("B", "Ana", "Soto", "Vega", "1980-07-04", 44, "F", mun="Ponce"),
        # Near-dup (real spelling typo)
        _row("C", "Carmen", "Morales", "Reyes", "1972-02-28", 52, "F", mun="Caguas"),
        _row("D", "Carmin", "Morales", "Reyes", "1972-02-28", 52, "F", mun="Caguas"),
        # Clean record
        _row("E", "Sonia", "Vázquez", "Negrón", "1995-09-11", 29, "F"),
        # Deceased
        _row("F", "Agustín", "Cruz", "Santos", "1910-03-21", 114, mun="Mayagüez"),
    ])
    records = scan_db_table(_config(db))
    all_groups = run_full_match(records)
    flags = {g.flag for g in all_groups}

    assert "EXACT_DUPLICATE" in flags
    assert "POSSIBLE_DECEASED" in flags

    exact = [g for g in all_groups if g.flag == "EXACT_DUPLICATE"]
    assert len(exact) == 1
    assert len(exact[0].records) == 2

    deceased = [g for g in all_groups if g.flag == "POSSIBLE_DECEASED"]
    assert len(deceased) == 1
    assert deceased[0].records[0].row_id == "F"
