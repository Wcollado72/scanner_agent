"""
tests/test_address_analyzer.py
Tests for scanner_agent.detection.address_analyzer

Covers:
  - normalize_pr_address:   canonical form output
  - classify_address_type:  correct string type for each address pattern
  - find_address_clusters:  correct flags, confidence, count, and metadata
"""

import pytest
from scanner_agent.detection.address_analyzer import (
    normalize_pr_address,
    classify_address_type,
    find_address_clusters,
)
from scanner_agent.models import RowRecord


# ── Helpers ────────────────────────────────────────────────────────────────

def _row(row_id, address, municipio="San Juan"):
    return RowRecord(
        row_id=row_id,
        source_table="electores",
        source_db="test",
        nombre="Test",
        apellido_paterno="Record",
        apellido_materno="Dummy",
        fecha_nacimiento="1980-01-01",
        edad=44,
        municipio=municipio,
        direccion=address,
    )


def _rows(n, address, municipio="San Juan", id_prefix="R"):
    return [_row(f"{id_prefix}{i:03d}", address, municipio) for i in range(1, n + 1)]


# ── normalize_pr_address ───────────────────────────────────────────────────

class TestNormalizePrAddress:

    def test_strips_accents(self):
        result = normalize_pr_address("Calle Pérez #3")
        assert "PEREZ" in result
        assert "É" not in result

    def test_uppercases_output(self):
        result = normalize_pr_address("calle loiza 12")
        assert result == result.upper()

    def test_c_slash_becomes_calle(self):
        result = normalize_pr_address("C/ Sol #5")
        assert "CALLE" in result
        assert "C/" not in result

    def test_ave_normalized(self):
        assert "AVE" in normalize_pr_address("Ave. Ponce de Leon 100")

    def test_avenida_normalized(self):
        assert "AVE" in normalize_pr_address("Avenida Fernandez Juncos 55")

    def test_av_dot_normalized(self):
        # Av. → removes period → AV → should map to AVE
        result = normalize_pr_address("Av. De Diego 200")
        assert "AVE" in result

    def test_urb_prefix_preserved(self):
        result = normalize_pr_address("Urbanizacion Santa Rosa Calle 5 #10")
        assert "URB" in result

    def test_apartamento_to_apt(self):
        result = normalize_pr_address("Calle Luna Apartamento 3")
        assert "APT" in result
        assert "APARTAMENTO" not in result

    def test_po_box_normalized(self):
        result = normalize_pr_address("PO Box 1234")
        assert "PO" in result and ("BOX" in result or "1234" in result)

    def test_removes_commas(self):
        result = normalize_pr_address("Calle Fortaleza, #22")
        assert "," not in result

    def test_empty_string_returns_empty(self):
        assert normalize_pr_address("") == ""

    def test_none_returns_empty(self):
        assert normalize_pr_address(None) == ""

    def test_extra_whitespace_collapsed(self):
        result = normalize_pr_address("  Calle   Sol   #3  ")
        assert "  " not in result.strip()

    def test_barrio_to_bo(self):
        result = normalize_pr_address("Barrio Obrero Calle A")
        assert "BO" in result
        assert "BARRIO" not in result

    def test_condominio_to_cond(self):
        result = normalize_pr_address("Condominio Torres del Mar Apt 5A")
        assert "COND" in result
        assert "CONDOMINIO" not in result


# ── classify_address_type ──────────────────────────────────────────────────

class TestClassifyAddressType:

    def test_residential_calle(self):
        n = normalize_pr_address("Calle Luna #15")
        assert classify_address_type(n) == "RESIDENTIAL"

    def test_residential_ave(self):
        n = normalize_pr_address("Ave. Ponce de Leon 123")
        assert classify_address_type(n) == "RESIDENTIAL"

    def test_urbanization(self):
        n = normalize_pr_address("Urb. Santa Rosa Calle Orquidea #7")
        assert classify_address_type(n) == "URBANIZATION"

    def test_apartment_cond(self):
        n = normalize_pr_address("Condominio Torres del Mar Apt 5A")
        assert classify_address_type(n) == "APARTMENT"

    def test_rural_hc(self):
        n = normalize_pr_address("HC-02 Box 4512")
        assert classify_address_type(n) == "RURAL"

    def test_po_box(self):
        n = normalize_pr_address("PO Box 90210")
        assert classify_address_type(n) == "PO_BOX"

    def test_commercial_suite(self):
        n = normalize_pr_address("Calle Fortaleza 100 Suite 5")
        assert classify_address_type(n) == "COMMERCIAL"


# ── find_address_clusters ──────────────────────────────────────────────────

class TestFindAddressClusters:

    def test_hard_threshold_residential_flagged(self):
        """25 voters at one residential address → hard threshold (conf 0.97)."""
        records = _rows(25, "Calle Sol #1", municipio="San Juan")
        groups = find_address_clusters(records)
        assert len(groups) == 1
        g = groups[0]
        assert g.flag == "ADDRESS_CLUSTER"
        assert g.confidence == pytest.approx(0.97)
        assert len(g.records) == 25

    def test_soft_threshold_residential_flagged(self):
        """10 voters at one residential address → soft flag (conf 0.75)."""
        records = _rows(10, "Calle Loiza #99", municipio="Bayamon")
        groups = find_address_clusters(records)
        assert len(groups) == 1
        g = groups[0]
        assert g.flag == "ADDRESS_CLUSTER"
        assert g.confidence == pytest.approx(0.75)

    def test_below_soft_threshold_not_flagged(self):
        """4 voters at one residential address → not flagged (soft=8)."""
        records = _rows(4, "Calle Luna #5", municipio="Ponce")
        groups = find_address_clusters(records)
        assert len(groups) == 0

    def test_po_box_always_flagged_even_with_two(self):
        """2 voters at a PO Box → always flagged."""
        records = _rows(2, "PO Box 12345", municipio="Caguas")
        groups = find_address_clusters(records)
        assert len(groups) == 1
        assert groups[0].flag == "ADDRESS_CLUSTER"

    def test_commercial_invalid_type_confidence(self):
        """Commercial addresses are always flagged with CONF_INVALID_TYPE (0.90),
        regardless of count — electoral law prohibits voter registration at Suite/commercial."""
        records = _rows(8, "Ave Ponce de Leon 1050 Suite 301", municipio="San Juan")
        groups = find_address_clusters(records)
        assert len(groups) == 1
        # Commercial always triggers CONF_INVALID_TYPE (0.90), not the hard-count threshold
        assert groups[0].confidence == pytest.approx(0.90)

    def test_multiple_clusters_detected(self):
        """Two clusters + one clean group → exactly two groups returned."""
        r1 = _rows(25, "Calle Sol #1",    municipio="San Juan",  id_prefix="A")
        r2 = _rows(12, "PO Box 99",       municipio="Bayamon",   id_prefix="B")
        r3 = _rows(3,  "Calle Luna #2",   municipio="Ponce",     id_prefix="C")
        groups = find_address_clusters(r1 + r2 + r3)
        assert len(groups) == 2

    def test_same_address_different_municipio_separate_clusters(self):
        """Same address string in two different municipios = two distinct clusters."""
        r1 = _rows(25, "Calle Sol #1", municipio="San Juan",  id_prefix="SJ")
        r2 = _rows(25, "Calle Sol #1", municipio="Bayamon",   id_prefix="BA")
        groups = find_address_clusters(r1 + r2)
        assert len(groups) == 2

    def test_empty_records_returns_empty(self):
        assert find_address_clusters([]) == []

    def test_records_without_address_ignored(self):
        """Records with no direccion must not raise errors or produce false flags."""
        records = [_row(f"N{i}", None) for i in range(30)]
        groups = find_address_clusters(records)
        assert len(groups) == 0

    def test_strategy_is_address_concentration(self):
        records = _rows(25, "Calle Fortaleza #5", municipio="San Juan")
        groups = find_address_clusters(records)
        assert groups[0].strategy == "address_concentration"

    def test_notes_mention_count_or_location(self):
        records = _rows(25, "Calle Betances #77", municipio="Arecibo")
        groups = find_address_clusters(records)
        notes = groups[0].notes
        # Notes should mention the count and/or the municipio
        assert any(x in notes for x in ["25", "Arecibo", "CALLE", "BETANCES"])
