"""
tests/test_clean_exporter.py
Tests for scanner_agent.detection.clean_exporter

Covers:
  - get_flagged_ids:        extrae row_ids correctamente de findings
  - export_clean_records:   escribe solo registros sin flags al staging DB
  - load_staging_records:   recupera registros como RowRecord correctamente
  - get_staging_stats:      estadísticas correctas de staging DB
  - idempotencia:           exportar dos veces no duplica registros
  - filtro por source_db:   load_staging_records filtra correctamente
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from scanner_agent.detection.clean_exporter import (
    export_clean_records,
    get_flagged_ids,
    get_staging_stats,
    load_staging_records,
)
from scanner_agent.models import RowRecord


# ── Helpers ────────────────────────────────────────────────────────────────

def _rec(row_id: str, nombre: str = "Juan", source_db: str = "CEE",
         ssn: str | None = None, municipio: str = "San Juan") -> RowRecord:
    return RowRecord(
        row_id           = row_id,
        source_table     = "electores",
        source_db        = source_db,
        nombre           = nombre,
        apellido_paterno = "Perez",
        apellido_materno = "Rivera",
        fecha_nacimiento = "1975-06-15",
        edad             = 49,
        genero           = "M",
        direccion        = "Calle Sol #1",
        municipio        = municipio,
        seguro_social    = ssn or "***-**-1234",
        telefono         = "787-555-0001",
        tipo_telefono    = "Movil",
    )


def _finding(row_ids: list[str], flag: str = "EXACT_DUPLICATE") -> dict:
    return {
        "flag": flag,
        "strategy": "exact_match",
        "confidence": 1.0,
        "notes": "test finding",
        "records": [{"row_id": rid, "nombre_completo": "Juan Perez Rivera"} for rid in row_ids],
    }


# ── get_flagged_ids ────────────────────────────────────────────────────────

class TestGetFlaggedIds:
    def test_empty_findings(self):
        assert get_flagged_ids([]) == set()

    def test_single_finding_two_records(self):
        findings = [_finding(["E001", "E002"])]
        assert get_flagged_ids(findings) == {"E001", "E002"}

    def test_multiple_findings_no_overlap(self):
        findings = [_finding(["E001"]), _finding(["E002", "E003"])]
        result = get_flagged_ids(findings)
        assert result == {"E001", "E002", "E003"}

    def test_overlapping_records_deduplicated(self):
        # Same record appears in two findings
        findings = [_finding(["E001", "E002"]), _finding(["E001", "E003"])]
        result = get_flagged_ids(findings)
        assert result == {"E001", "E002", "E003"}

    def test_uses_row_id_field(self):
        finding = {"flag": "NEAR_DUPLICATE", "strategy": "fuzzy",
                   "confidence": 0.9, "notes": "",
                   "records": [{"row_id": "E999", "nombre_completo": "Test"}]}
        assert "E999" in get_flagged_ids([finding])

    def test_fallback_to_num_elector(self):
        # Some reports may use num_elector instead of row_id
        finding = {"flag": "NEAR_DUPLICATE", "strategy": "fuzzy",
                   "confidence": 0.9, "notes": "",
                   "records": [{"num_elector": "CEE-001", "nombre_completo": "Test"}]}
        assert "CEE-001" in get_flagged_ids([finding])

    def test_empty_row_id_ignored(self):
        finding = {"flag": "ANOMALY", "strategy": "age",
                   "confidence": 0.99, "notes": "",
                   "records": [{"row_id": "", "nombre_completo": "Ghost"}]}
        assert get_flagged_ids([finding]) == set()


# ── export_clean_records ───────────────────────────────────────────────────

class TestExportCleanRecords:
    def test_all_clean_exported(self, tmp_path):
        records = [_rec("E001"), _rec("E002"), _rec("E003")]
        dest = tmp_path / "staging.db"
        result = export_clean_records(records, set(), dest, "CEE", phase=2)

        assert result["total"] == 3
        assert result["exported"] == 3
        assert result["skipped_flagged"] == 0

    def test_flagged_records_omitted(self, tmp_path):
        records = [_rec("E001"), _rec("E002"), _rec("E003")]
        flagged = {"E001", "E003"}
        dest = tmp_path / "staging.db"
        result = export_clean_records(records, flagged, dest, "CEE", phase=2)

        assert result["exported"] == 1
        assert result["skipped_flagged"] == 2

    def test_staging_db_created(self, tmp_path):
        dest = tmp_path / "sub" / "staging.db"
        assert not dest.exists()
        export_clean_records([_rec("E001")], set(), dest, "CEE", phase=2)
        assert dest.exists()

    def test_records_written_to_db(self, tmp_path):
        records = [_rec("E001", nombre="Maria"), _rec("E002", nombre="Carlos")]
        dest = tmp_path / "staging.db"
        export_clean_records(records, set(), dest, "CEE", phase=2)

        conn = sqlite3.connect(str(dest))
        rows = conn.execute("SELECT row_id, nombre FROM registros_staging").fetchall()
        conn.close()

        ids = {r[0] for r in rows}
        assert "E001" in ids
        assert "E002" in ids

    def test_source_db_stored_uppercase(self, tmp_path):
        dest = tmp_path / "staging.db"
        export_clean_records([_rec("E001", source_db="cee")], set(), dest, "cee", phase=2)

        conn = sqlite3.connect(str(dest))
        source = conn.execute("SELECT source_db FROM registros_staging").fetchone()[0]
        conn.close()
        assert source == "CEE"

    def test_phase_stored(self, tmp_path):
        dest = tmp_path / "staging.db"
        export_clean_records([_rec("E001")], set(), dest, "RD", phase=1)

        conn = sqlite3.connect(str(dest))
        phase = conn.execute("SELECT pipeline_phase FROM registros_staging").fetchone()[0]
        conn.close()
        assert phase == 1

    def test_idempotent_no_duplicates(self, tmp_path):
        """Exportar el mismo registro dos veces no duplica en staging."""
        dest = tmp_path / "staging.db"
        records = [_rec("E001")]
        export_clean_records(records, set(), dest, "CEE", phase=2)
        result2 = export_clean_records(records, set(), dest, "CEE", phase=2)

        conn = sqlite3.connect(str(dest))
        count = conn.execute("SELECT COUNT(*) FROM registros_staging").fetchone()[0]
        conn.close()

        assert count == 1
        assert result2["skipped_duplicate"] == 1

    def test_different_sources_same_db(self, tmp_path):
        """RD y CEE records coexisten en el mismo staging DB."""
        dest = tmp_path / "staging.db"
        rd_records  = [_rec("RD-001", source_db="RD"), _rec("RD-002", source_db="RD")]
        cee_records = [_rec("E-001",  source_db="CEE"), _rec("E-002",  source_db="CEE")]

        export_clean_records(rd_records,  set(), dest, "RD",  phase=1)
        export_clean_records(cee_records, set(), dest, "CEE", phase=2)

        conn = sqlite3.connect(str(dest))
        count = conn.execute("SELECT COUNT(*) FROM registros_staging").fetchone()[0]
        conn.close()
        assert count == 4

    def test_empty_records_list(self, tmp_path):
        dest = tmp_path / "staging.db"
        result = export_clean_records([], set(), dest, "CEE", phase=2)
        assert result["total"] == 0
        assert result["exported"] == 0

    def test_all_flagged_nothing_exported(self, tmp_path):
        records = [_rec("E001"), _rec("E002")]
        dest = tmp_path / "staging.db"
        result = export_clean_records(records, {"E001", "E002"}, dest, "CEE", phase=2)
        assert result["exported"] == 0
        assert result["skipped_flagged"] == 2


# ── load_staging_records ───────────────────────────────────────────────────

class TestLoadStagingRecords:
    def _populate(self, dest: Path) -> None:
        rd_recs  = [_rec("RD-001", source_db="RD",  municipio="Caguas"),
                    _rec("RD-002", source_db="RD",  municipio="Ponce")]
        cee_recs = [_rec("E-001",  source_db="CEE", municipio="San Juan"),
                    _rec("E-002",  source_db="CEE", municipio="Bayamon"),
                    _rec("E-003",  source_db="CEE", municipio="Mayaguez")]
        export_clean_records(rd_recs,  set(), dest, "RD",  phase=1)
        export_clean_records(cee_recs, set(), dest, "CEE", phase=2)

    def test_load_all(self, tmp_path):
        dest = tmp_path / "staging.db"
        self._populate(dest)
        records = load_staging_records(dest)
        assert len(records) == 5

    def test_load_filter_rd(self, tmp_path):
        dest = tmp_path / "staging.db"
        self._populate(dest)
        records = load_staging_records(dest, source_filter="RD")
        assert len(records) == 2
        assert all(r.source_db == "RD" for r in records)

    def test_load_filter_cee(self, tmp_path):
        dest = tmp_path / "staging.db"
        self._populate(dest)
        records = load_staging_records(dest, source_filter="CEE")
        assert len(records) == 3
        assert all(r.source_db == "CEE" for r in records)

    def test_returns_rowrecord_instances(self, tmp_path):
        dest = tmp_path / "staging.db"
        export_clean_records([_rec("E001")], set(), dest, "CEE", phase=2)
        records = load_staging_records(dest)
        assert len(records) == 1
        assert isinstance(records[0], RowRecord)

    def test_fields_preserved(self, tmp_path):
        dest = tmp_path / "staging.db"
        original = _rec("E001", nombre="Rosa", municipio="Guaynabo", ssn="***-**-9876")
        export_clean_records([original], set(), dest, "CEE", phase=2)

        loaded = load_staging_records(dest)[0]
        assert loaded.nombre == "Rosa"
        assert loaded.municipio == "Guaynabo"
        assert loaded.seguro_social == "***-**-9876"
        assert loaded.apellido_paterno == "Perez"

    def test_nonexistent_db_returns_empty(self, tmp_path):
        records = load_staging_records(tmp_path / "does_not_exist.db")
        assert records == []

    def test_source_db_preserved(self, tmp_path):
        dest = tmp_path / "staging.db"
        export_clean_records([_rec("RD-001", source_db="RD")], set(), dest, "RD", phase=1)
        loaded = load_staging_records(dest)[0]
        assert loaded.source_db == "RD"


# ── get_staging_stats ──────────────────────────────────────────────────────

class TestGetStagingStats:
    def test_empty_db_returns_zeros(self, tmp_path):
        dest = tmp_path / "staging.db"
        # Create empty DB
        export_clean_records([], set(), dest, "CEE", phase=2)
        stats = get_staging_stats(dest)
        assert stats["total"] == 0
        assert stats["por_fuente"] == {}

    def test_nonexistent_db(self, tmp_path):
        stats = get_staging_stats(tmp_path / "nope.db")
        assert stats["total"] == 0

    def test_counts_by_source(self, tmp_path):
        dest = tmp_path / "staging.db"
        export_clean_records([_rec("R1", source_db="RD"), _rec("R2", source_db="RD")],
                             set(), dest, "RD", phase=1)
        export_clean_records([_rec("E1", source_db="CEE"), _rec("E2", source_db="CEE"),
                              _rec("E3", source_db="CEE")],
                             set(), dest, "CEE", phase=2)

        stats = get_staging_stats(dest)
        assert stats["total"] == 5
        assert stats["por_fuente"]["RD"]  == 2
        assert stats["por_fuente"]["CEE"] == 3

    def test_counts_by_phase(self, tmp_path):
        dest = tmp_path / "staging.db"
        export_clean_records([_rec("R1", source_db="RD")],  set(), dest, "RD",  phase=1)
        export_clean_records([_rec("E1", source_db="CEE")], set(), dest, "CEE", phase=2)

        stats = get_staging_stats(dest)
        assert stats["por_fase"][1] == 1
        assert stats["por_fase"][2] == 1


# ── Pipeline integration (end-to-end light) ───────────────────────────────

class TestPipelineIntegration:
    """
    Smoke test: simula Fases 1+2 del pipeline y verifica que el staging
    contiene exactamente los registros limpios de ambas fuentes.
    """

    def test_phase1_rd_phase2_cee_staging(self, tmp_path):
        dest = tmp_path / "rd_cee_depurado.db"

        rd_records = [
            _rec("RD-001", nombre="Diana",   source_db="RD"),
            _rec("RD-002", nombre="Miguel",  source_db="RD"),
            _rec("RD-003", nombre="Flagged", source_db="RD"),  # este tiene flag
        ]
        cee_records = [
            _rec("E-001", nombre="Luisa",   source_db="CEE"),
            _rec("E-002", nombre="Roberto", source_db="CEE"),
        ]

        # Fase 1: RD — RD-003 tiene flag
        rd_findings = [_finding(["RD-003"], flag="POSSIBLE_DECEASED")]
        rd_flagged  = get_flagged_ids(rd_findings)
        r1 = export_clean_records(rd_records, rd_flagged, dest, "RD", phase=1)

        # Fase 2: CEE — sin flags
        r2 = export_clean_records(cee_records, set(), dest, "CEE", phase=2)

        assert r1["exported"] == 2
        assert r1["skipped_flagged"] == 1
        assert r2["exported"] == 2

        # Staging contiene exactamente 4 registros limpios
        all_clean = load_staging_records(dest)
        assert len(all_clean) == 4

        # Separados correctamente por fuente
        rd_clean  = load_staging_records(dest, source_filter="RD")
        cee_clean = load_staging_records(dest, source_filter="CEE")
        assert len(rd_clean)  == 2
        assert len(cee_clean) == 2

        # El registro flaggeado NO está en staging
        all_ids = {r.row_id for r in all_clean}
        assert "RD-003" not in all_ids

    def test_staging_ready_for_cross_match(self, tmp_path):
        """
        Los registros del staging son RowRecord válidos listos para
        pasarse a run_cross_db_match o run_full_match.
        """
        dest = tmp_path / "staging.db"
        records = [
            _rec("E-001", nombre="Ana",  source_db="CEE", ssn="***-**-1111"),
            _rec("E-002", nombre="Luis", source_db="CEE", ssn="***-**-2222"),
        ]
        export_clean_records(records, set(), dest, "CEE", phase=2)

        loaded = load_staging_records(dest)
        for r in loaded:
            # Todos los campos críticos deben estar presentes
            assert r.row_id
            assert r.nombre
            assert r.apellido_paterno
            assert r.source_db
            assert r.source_table
            # nombre_completo debe funcionar
            assert "Perez" in r.nombre_completo
