"""
tests/test_nomina_scanner.py
Tests para scanner_agent.detection.nomina_scanner

Cubre:
  - scan_multi_agency:       SSN en >=2 agencias, solo activos, confianza 1.0
  - scan_deceased_employees: cruce SSN con RD, empleados activos vs. inactivos
  - scan_salary_anomalies:   z-score por categoría, umbral configurable
  - run_nomina_scan:         orquestador, serialización JSON, summary correcto
  - edge cases:              listas vacías, un solo registro, salario None
"""

import pytest

from scanner_agent.models import AuditDomain, AuditFlag, RowRecord
from scanner_agent.detection.nomina_scanner import (
    run_nomina_scan,
    scan_deceased_employees,
    scan_multi_agency,
    scan_salary_anomalies,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _emp(emp_id, ssn="***-**-1234", agencia="EDUCACION",
         categoria="MAESTRO", salario=50_000.0,
         fecha_fin=None, activo=True) -> RowRecord:
    return RowRecord(
        row_id           = emp_id,
        source_table     = "nomina_empleados",
        source_db        = "NOMINA",
        source_domain    = AuditDomain.NOMINA,
        nombre           = "Juan",
        apellido_paterno = "Perez",
        apellido_materno = "Rivera",
        seguro_social    = ssn,
        agencia          = agencia,
        puesto           = categoria,
        salario          = salario,
        fecha_inicio_empleo = "2010-01-01",
        fecha_fin_empleo = None if activo else "2022-12-31",
        municipio        = "San Juan",
        fecha_nacimiento = "1980-05-15",
        genero           = "M",
    )


def _fallecido(cert_num, ssn) -> RowRecord:
    return RowRecord(
        row_id           = cert_num,
        source_table     = "defunciones",
        source_db        = "RD",
        nombre           = "Juan",
        apellido_paterno = "Perez",
        apellido_materno = "Rivera",
        seguro_social    = ssn,
        fecha_nacimiento = "2022-03-15",
    )


# ── scan_multi_agency ──────────────────────────────────────────────────────

class TestScanMultiAgency:
    def test_empty_returns_empty(self):
        assert scan_multi_agency([]) == []

    def test_single_employee_no_flag(self):
        assert scan_multi_agency([_emp("E001")]) == []

    def test_same_agency_no_flag(self):
        recs = [_emp("E001", ssn="***-**-1111", agencia="SALUD"),
                _emp("E002", ssn="***-**-1111", agencia="SALUD")]
        assert scan_multi_agency(recs) == []

    def test_two_agencies_flagged(self):
        recs = [_emp("E001", ssn="***-**-2222", agencia="SALUD"),
                _emp("E002", ssn="***-**-2222", agencia="EDUCACION")]
        groups = scan_multi_agency(recs)
        assert len(groups) == 1
        assert groups[0].flag == AuditFlag.MULTI_AGENCY_EMPLOYEE

    def test_confidence_is_1(self):
        recs = [_emp("E001", ssn="***-**-3333", agencia="SALUD"),
                _emp("E002", ssn="***-**-3333", agencia="HACIENDA")]
        assert scan_multi_agency(recs)[0].confidence == 1.0

    def test_domain_is_nomina(self):
        recs = [_emp("E001", ssn="***-**-4444", agencia="SALUD"),
                _emp("E002", ssn="***-**-4444", agencia="HACIENDA")]
        assert scan_multi_agency(recs)[0].domain == AuditDomain.NOMINA

    def test_records_included_in_group(self):
        recs = [_emp("E001", ssn="***-**-5555", agencia="SALUD"),
                _emp("E002", ssn="***-**-5555", agencia="HACIENDA")]
        group = scan_multi_agency(recs)[0]
        ids = {r.row_id for r in group.records}
        assert ids == {"E001", "E002"}

    def test_inactive_employee_excluded(self):
        """Un empleado inactivo (fecha_fin seteada) no debe generar flag."""
        recs = [_emp("E001", ssn="***-**-6666", agencia="SALUD", activo=False),
                _emp("E002", ssn="***-**-6666", agencia="HACIENDA")]
        # Solo E002 está activo → solo 1 agencia activa → no hay flag
        assert scan_multi_agency(recs) == []

    def test_both_active_two_agencies_flagged(self):
        recs = [_emp("E001", ssn="***-**-7777", agencia="SALUD"),
                _emp("E002", ssn="***-**-7777", agencia="HACIENDA"),
                _emp("E003", ssn="***-**-8888", agencia="EDUCACION")]
        groups = scan_multi_agency(recs)
        assert len(groups) == 1   # solo el SSN 7777 tiene 2 agencias

    def test_three_agencies_one_group(self):
        """Mismo SSN en 3 agencias → 1 grupo con 3 registros."""
        ssn = "***-**-9999"
        recs = [_emp(f"E{i:03d}", ssn=ssn, agencia=ag)
                for i, ag in enumerate(["SALUD","HACIENDA","JUSTICIA"])]
        groups = scan_multi_agency(recs)
        assert len(groups) == 1
        assert len(groups[0].records) == 3

    def test_notes_contain_agencias(self):
        recs = [_emp("E001", ssn="***-**-1010", agencia="SALUD"),
                _emp("E002", ssn="***-**-1010", agencia="HACIENDA")]
        notes = scan_multi_agency(recs)[0].notes
        assert "SALUD" in notes or "HACIENDA" in notes

    def test_no_ssn_skipped(self):
        """Registros sin SSN no deben causar errores ni falsos positivos."""
        recs = [_emp("E001", ssn=None, agencia="SALUD"),
                _emp("E002", ssn=None, agencia="HACIENDA")]
        assert scan_multi_agency(recs) == []


# ── scan_deceased_employees ────────────────────────────────────────────────

class TestScanDeceasedEmployees:
    def test_empty_inputs(self):
        assert scan_deceased_employees([], []) == []

    def test_no_rd_records(self):
        assert scan_deceased_employees([_emp("E001")], []) == []

    def test_no_match(self):
        nomina = [_emp("E001", ssn="***-**-1111")]
        rd     = [_fallecido("RD-001", "***-**-9999")]
        assert scan_deceased_employees(nomina, rd) == []

    def test_match_flags_deceased(self):
        ssn    = "***-**-2222"
        nomina = [_emp("E001", ssn=ssn)]
        rd     = [_fallecido("RD-001", ssn)]
        groups = scan_deceased_employees(nomina, rd)
        assert len(groups) == 1
        assert groups[0].flag == AuditFlag.DECEASED_EMPLOYEE

    def test_confidence_097(self):
        ssn    = "***-**-3333"
        nomina = [_emp("E001", ssn=ssn)]
        rd     = [_fallecido("RD-001", ssn)]
        assert scan_deceased_employees(nomina, rd)[0].confidence == 0.97

    def test_domain_is_nomina(self):
        ssn    = "***-**-4444"
        nomina = [_emp("E001", ssn=ssn)]
        rd     = [_fallecido("RD-001", ssn)]
        assert scan_deceased_employees(nomina, rd)[0].domain == AuditDomain.NOMINA

    def test_inactive_employee_not_flagged(self):
        """Empleado ya dado de baja no debe generar flag DECEASED_EMPLOYEE."""
        ssn    = "***-**-5555"
        nomina = [_emp("E001", ssn=ssn, activo=False)]
        rd     = [_fallecido("RD-001", ssn)]
        assert scan_deceased_employees(nomina, rd) == []

    def test_multiple_matches(self):
        nomina = [_emp(f"E{i:03d}", ssn=f"***-**-{6600+i}") for i in range(3)]
        rd     = [_fallecido(f"RD-{i:03d}", f"***-**-{6600+i}") for i in range(3)]
        groups = scan_deceased_employees(nomina, rd)
        assert len(groups) == 3

    def test_group_contains_both_records(self):
        """El grupo debe incluir el registro de nómina Y el de RD."""
        ssn    = "***-**-7777"
        emp    = _emp("E001", ssn=ssn)
        fal    = _fallecido("RD-001", ssn)
        group  = scan_deceased_employees([emp], [fal])[0]
        sources = {r.source_db for r in group.records}
        assert "NOMINA" in sources
        assert "RD" in sources

    def test_no_ssn_in_nomina_skipped(self):
        nomina = [_emp("E001", ssn=None)]
        rd     = [_fallecido("RD-001", "***-**-8888")]
        assert scan_deceased_employees(nomina, rd) == []


# ── scan_salary_anomalies ──────────────────────────────────────────────────

class TestScanSalaryAnomalies:
    def _build_cat(self, base, n_normal=10, outlier=None):
        """Genera n registros de la misma categoría + opcionalmente uno anómalo."""
        recs = [_emp(f"E{i:03d}", ssn=f"***-**-{1000+i}",
                     categoria="MAESTRO", salario=base + i * 100)
                for i in range(n_normal)]
        if outlier is not None:
            recs.append(_emp("OUTLIER", ssn="***-**-9999",
                             categoria="MAESTRO", salario=outlier))
        return recs

    def test_empty_returns_empty(self):
        assert scan_salary_anomalies([]) == []

    def test_single_record_no_flag(self):
        assert scan_salary_anomalies([_emp("E001")]) == []

    def test_two_records_no_flag(self):
        """Con <3 registros por categoría no hay estadísticas suficientes."""
        recs = [_emp("E001", salario=50_000), _emp("E002", salario=51_000)]
        assert scan_salary_anomalies(recs) == []

    def test_normal_distribution_no_flags(self):
        """Salarios dentro de rango normal no deben disparar flags."""
        recs = self._build_cat(50_000, n_normal=15)
        assert scan_salary_anomalies(recs) == []

    def test_outlier_detected(self):
        """Un salario de 5x la media debe generar flag."""
        recs = self._build_cat(50_000, n_normal=15, outlier=350_000)
        groups = scan_salary_anomalies(recs)
        assert len(groups) >= 1
        assert any(g.flag == AuditFlag.SALARY_ANOMALY for g in groups)

    def test_outlier_record_identified(self):
        recs = self._build_cat(50_000, n_normal=15, outlier=350_000)
        groups = scan_salary_anomalies(recs)
        flagged_ids = {r.row_id for g in groups for r in g.records}
        assert "OUTLIER" in flagged_ids

    def test_domain_is_nomina(self):
        recs = self._build_cat(50_000, n_normal=15, outlier=350_000)
        groups = scan_salary_anomalies(recs)
        assert all(g.domain == AuditDomain.NOMINA for g in groups)

    def test_flag_is_salary_anomaly(self):
        recs = self._build_cat(50_000, n_normal=15, outlier=350_000)
        groups = scan_salary_anomalies(recs)
        assert all(g.flag == AuditFlag.SALARY_ANOMALY for g in groups)

    def test_custom_threshold_stricter(self):
        """Umbral más bajo detecta más anomalías."""
        recs = self._build_cat(50_000, n_normal=15, outlier=120_000)
        default_groups = scan_salary_anomalies(recs, zscore_threshold=2.5)
        strict_groups  = scan_salary_anomalies(recs, zscore_threshold=1.0)
        assert len(strict_groups) >= len(default_groups)

    def test_salary_none_skipped(self):
        """Registros con salario None no deben causar errores."""
        recs = [_emp(f"E{i:03d}", ssn=f"***-**-{2000+i}",
                     categoria="AUDITOR", salario=None)
                for i in range(5)]
        result = scan_salary_anomalies(recs)
        assert result == []

    def test_notes_contain_salary(self):
        recs = self._build_cat(50_000, n_normal=15, outlier=350_000)
        groups = scan_salary_anomalies(recs)
        assert any("350,000" in g.notes or "350000" in g.notes.replace(",","")
                   for g in groups)


# ── run_nomina_scan ────────────────────────────────────────────────────────

class TestRunNominaScan:
    def test_empty_scan(self):
        result = run_nomina_scan([])
        assert result["total_records"] == 0
        assert result["findings"] == []
        assert result["summary"]["MULTI_AGENCY_EMPLOYEE"] == 0

    def test_all_clean(self):
        recs = [_emp(f"E{i:03d}", ssn=f"***-**-{3000+i}",
                     categoria="MAESTRO", salario=50_000 + i * 200)
                for i in range(20)]
        result = run_nomina_scan(recs)
        assert result["summary"]["total_flagged_records"] == 0

    def test_returns_total_records(self):
        recs = [_emp(f"E{i:03d}", ssn=f"***-**-{4000+i}") for i in range(10)]
        assert run_nomina_scan(recs)["total_records"] == 10

    def test_findings_serializable(self):
        """Todos los findings deben ser dicts con campos requeridos."""
        recs = [_emp("E001", ssn="***-**-5555", agencia="SALUD"),
                _emp("E002", ssn="***-**-5555", agencia="HACIENDA")]
        result = run_nomina_scan(recs)
        for f in result["findings"]:
            assert "flag" in f
            assert "confidence" in f
            assert "records" in f
            assert "notes" in f
            assert "domain" in f

    def test_summary_keys_present(self):
        result = run_nomina_scan([])
        s = result["summary"]
        assert AuditFlag.MULTI_AGENCY_EMPLOYEE in s
        assert AuditFlag.DECEASED_EMPLOYEE in s
        assert AuditFlag.SALARY_ANOMALY in s
        assert "total_flagged_records" in s

    def test_without_rd_no_deceased_scan(self):
        """Sin rd_records, DECEASED_EMPLOYEE debe ser 0."""
        ssn  = "***-**-6666"
        recs = [_emp("E001", ssn=ssn)]
        result = run_nomina_scan(recs, rd_records=None)
        assert result["summary"][AuditFlag.DECEASED_EMPLOYEE] == 0

    def test_with_rd_detects_deceased(self):
        ssn  = "***-**-7777"
        recs = [_emp("E001", ssn=ssn)]
        rd   = [_fallecido("RD-001", ssn)]
        result = run_nomina_scan(recs, rd_records=rd)
        assert result["summary"][AuditFlag.DECEASED_EMPLOYEE] == 1

    def test_combined_flags(self):
        """Multi-agency + deceased en el mismo escaneo."""
        ssn_multi = "***-**-8888"
        ssn_dead  = "***-**-9191"
        recs = [
            _emp("E001", ssn=ssn_multi, agencia="SALUD",    categoria="MAESTRO", salario=50_000),
            _emp("E002", ssn=ssn_multi, agencia="HACIENDA", categoria="MAESTRO", salario=50_000),
            _emp("E003", ssn=ssn_dead,  agencia="EDUCACION",categoria="MAESTRO", salario=50_000),
        ]
        rd = [_fallecido("RD-001", ssn_dead)]
        result = run_nomina_scan(recs, rd_records=rd)
        assert result["summary"][AuditFlag.MULTI_AGENCY_EMPLOYEE] == 1
        assert result["summary"][AuditFlag.DECEASED_EMPLOYEE]     == 1
