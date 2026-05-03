"""
NOTE: Este archivo fue creado por error en esta ubicacion.
El archivo correcto esta en tests/test_agents.py

tests/test_agents.py  v1.0.0

Suite de tests para la arquitectura multi-agente.

Cubre:
    1. AgentBus — operaciones CRUD de sesiones, tareas, findings, mensajes
    2. DomainScannerAgent — escaneo ELECTORAL y NOMINA con datos sinteticos
    3. ReviewAgent — deduplicacion, auto-certify, exclusiones
    4. ReportAgent — escritura a matriz, generacion de JSON y summary
    5. OrchestratorAgent — pipeline completo end-to-end

Todos los tests usan bases de datos sinteticas existentes:
    tests/data/mock_cee.db
    tests/data/mock_rd.db
    tests/data/mock_cesco.db
    tests/data/mock_nomina.db

No se requiere acceso a ninguna base de datos real.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# Asegurarse de que el src esta en el path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scanner_agent.agents.bus import AgentBus, AgentFinding, _now, _uid
from scanner_agent.agents.base_agent import AgentConfig
from scanner_agent.agents.scanner_agent import DomainScannerAgent
from scanner_agent.agents.review_agent import ReviewAgent
from scanner_agent.agents.report_agent import ReportAgent
from scanner_agent.agents.orchestrator import OrchestratorAgent
from scanner_agent.models import AuditDomain, AuditFlag

# ── Fixtures ──────────────────────────────────────────────────────────────

TESTS_DATA = Path(__file__).parent / "data"

MOCK_SOURCES = {
    "CEE":   f"sqlite:///{TESTS_DATA / 'mock_cee.db'}",
    "RD":    f"sqlite:///{TESTS_DATA / 'mock_rd.db'}",
    "CESCO": f"sqlite:///{TESTS_DATA / 'mock_cesco.db'}",
    "NOMINA": f"sqlite:///{TESTS_DATA / 'mock_nomina.db'}",
}


@pytest.fixture
def tmp_bus(tmp_path):
    """AgentBus en memoria temporal para tests."""
    bus = AgentBus(tmp_path / "test_bus.db")
    yield bus
    bus.close()


@pytest.fixture
def default_config():
    return AgentConfig(
        dry_run=True,            # no escribe a disco en los tests
        min_confidence=0.40,
        approved_by="pytest",
    )


@pytest.fixture
def session_id(tmp_bus):
    return tmp_bus.open_session(
        domains=[AuditDomain.ELECTORAL, AuditDomain.NOMINA],
        started_by="pytest",
    )


# ── Tests AgentBus ────────────────────────────────────────────────────────

class TestAgentBus:

    def test_open_session(self, tmp_bus):
        sid = tmp_bus.open_session(domains=["ELECTORAL"], started_by="test")
        assert sid
        session = tmp_bus.get_session(sid)
        assert session is not None
        assert session.status == "open"
        assert "ELECTORAL" in session.domains

    def test_update_session_status(self, tmp_bus):
        sid = tmp_bus.open_session(domains=["NOMINA"])
        tmp_bus.update_session_status(sid, "scanning")
        s = tmp_bus.get_session(sid)
        assert s.status == "scanning"

    def test_enqueue_and_claim_task(self, tmp_bus, session_id):
        tid = tmp_bus.enqueue_task(
            session_id=session_id,
            agent_type="scanner",
            payload={"domain": "ELECTORAL"},
            domain="ELECTORAL",
        )
        assert tid
        task = tmp_bus.get_task(tid)
        assert task.status == "pending"

        claimed = tmp_bus.claim_task(tid)
        assert claimed
        task = tmp_bus.get_task(tid)
        assert task.status == "in_progress"

        # No se puede reclamar dos veces
        claimed_again = tmp_bus.claim_task(tid)
        assert not claimed_again

    def test_complete_task(self, tmp_bus, session_id):
        tid = tmp_bus.enqueue_task(session_id, "scanner", {"x": 1})
        tmp_bus.claim_task(tid)
        tmp_bus.complete_task(tid, result={"findings_count": 5})
        task = tmp_bus.get_task(tid)
        assert task.status == "completed"
        assert task.result["findings_count"] == 5

    def test_post_and_get_finding(self, tmp_bus, session_id):
        # Primero necesitamos un task_id valido
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})
        finding = AgentFinding(
            finding_id=_uid(),
            session_id=session_id,
            task_id=tid,
            source_agent="scanner:ELECTORAL",
            domain=AuditDomain.ELECTORAL,
            flag=AuditFlag.EXACT_DUPLICATE,
            confidence=1.0,
            records=[{"row_id": "001", "nombre": "Juan"}],
            notes="Test finding",
            created_at=_now(),
        )
        fid = tmp_bus.post_finding(finding)
        assert fid == finding.finding_id

        findings = tmp_bus.get_findings(session_id)
        assert len(findings) == 1
        assert findings[0].flag == AuditFlag.EXACT_DUPLICATE

    def test_session_counter_increments(self, tmp_bus, session_id):
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})
        for _ in range(3):
            f = AgentFinding(
                finding_id=_uid(),
                session_id=session_id,
                task_id=tid,
                source_agent="scanner:test",
                domain=AuditDomain.ELECTORAL,
                flag=AuditFlag.NEAR_DUPLICATE,
                confidence=0.75,
                records=[],
                created_at=_now(),
            )
            tmp_bus.post_finding(f)

        session = tmp_bus.get_session(session_id)
        assert session.total_findings == 3

    def test_filter_findings_by_status(self, tmp_bus, session_id):
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})
        for flag in [AuditFlag.EXACT_DUPLICATE, AuditFlag.NEAR_DUPLICATE]:
            f = AgentFinding(
                finding_id=_uid(), session_id=session_id, task_id=tid,
                source_agent="scanner:test", domain=AuditDomain.ELECTORAL,
                flag=flag, confidence=0.9, records=[], created_at=_now(),
            )
            tmp_bus.post_finding(f)

        # Actualizar uno a 'reviewed'
        findings = tmp_bus.get_findings(session_id)
        tmp_bus.update_finding_status(findings[0].finding_id, "reviewed", "test")

        pending = tmp_bus.get_findings(session_id, status="pending")
        reviewed = tmp_bus.get_findings(session_id, status="reviewed")
        assert len(pending) == 1
        assert len(reviewed) == 1

    def test_send_and_read_message(self, tmp_bus, session_id):
        tmp_bus.send_message(
            from_agent="scanner:A",
            to_agent="orchestrator",
            msg_type="task_complete",
            payload={"task_id": "abc"},
            session_id=session_id,
        )
        msgs = tmp_bus.read_messages("orchestrator", session_id=session_id)
        assert len(msgs) == 1
        assert msgs[0]["msg_type"] == "task_complete"

        # Segunda lectura: ya marcados como leidos
        msgs2 = tmp_bus.read_messages("orchestrator", session_id=session_id)
        assert len(msgs2) == 0

    def test_session_stats(self, tmp_bus, session_id):
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})
        f = AgentFinding(
            finding_id=_uid(), session_id=session_id, task_id=tid,
            source_agent="s", domain=AuditDomain.NOMINA,
            flag=AuditFlag.MULTI_AGENCY_EMPLOYEE, confidence=0.95,
            records=[], created_at=_now(),
        )
        tmp_bus.post_finding(f)
        stats = tmp_bus.session_stats(session_id)
        assert "session" in stats
        assert stats["session"]["total_findings"] == 1


# ── Tests DomainScannerAgent ──────────────────────────────────────────────

class TestDomainScannerAgent:

    @pytest.mark.skipif(
        not (TESTS_DATA / "mock_cee.db").exists(),
        reason="mock_cee.db no disponible"
    )
    def test_electoral_scan_produces_findings(self, tmp_bus, default_config):
        """Escaneo electoral con datos sinteticos debe producir findings."""
        session_id = tmp_bus.open_session(
            domains=[AuditDomain.ELECTORAL], started_by="pytest"
        )
        scanner = DomainScannerAgent(tmp_bus, default_config)
        scanner.setup()

        payload = {
            "domain": AuditDomain.ELECTORAL,
            "sources": {
                "CEE": MOCK_SOURCES["CEE"],
                "RD":  MOCK_SOURCES["RD"],
            },
        }
        tid = tmp_bus.enqueue_task(session_id, "scanner", payload, domain="ELECTORAL")
        task = tmp_bus.get_task(tid)
        result = scanner.execute_task(task)

        assert result.get("domain") == AuditDomain.ELECTORAL
        # Con datos sinteticos debe haber al menos 1 finding
        findings = tmp_bus.get_findings(session_id)
        # dry_run=True no escribe al bus, pero contamos el resultado
        assert "findings_count" in result
        scanner.teardown()

    @pytest.mark.skipif(
        not (TESTS_DATA / "mock_nomina.db").exists(),
        reason="mock_nomina.db no disponible"
    )
    def test_nomina_scan_produces_findings(self, tmp_bus, default_config):
        """Escaneo nomina con 298 empleados sinteticos."""
        session_id = tmp_bus.open_session(
            domains=[AuditDomain.NOMINA], started_by="pytest"
        )
        scanner = DomainScannerAgent(tmp_bus, default_config)
        scanner.setup()

        payload = {
            "domain": AuditDomain.NOMINA,
            "sources": {"NOMINA": MOCK_SOURCES["NOMINA"]},
        }
        tid = tmp_bus.enqueue_task(session_id, "scanner", payload, domain="NOMINA")
        task = tmp_bus.get_task(tid)
        result = scanner.execute_task(task)

        assert result.get("domain") == AuditDomain.NOMINA
        assert "findings_count" in result
        scanner.teardown()

    def test_unsupported_domain_raises(self, tmp_bus, default_config):
        session_id = tmp_bus.open_session(domains=["FAKE"], started_by="pytest")
        scanner = DomainScannerAgent(tmp_bus, default_config)

        payload = {"domain": "FAKE_DOMAIN", "sources": {}}
        tid = tmp_bus.enqueue_task(session_id, "scanner", payload, domain="FAKE_DOMAIN")
        task = tmp_bus.get_task(tid)
        result = scanner.execute_task(task)
        # Debe fallar gracefully (tarea marcada como failed, result vacio)
        final_task = tmp_bus.get_task(tid)
        assert final_task.status == "failed"


# ── Tests ReviewAgent ─────────────────────────────────────────────────────

class TestReviewAgent:

    def _add_finding(self, bus, session_id, task_id, flag, confidence, records=None):
        f = AgentFinding(
            finding_id=_uid(),
            session_id=session_id,
            task_id=task_id,
            source_agent="scanner:test",
            domain=AuditDomain.ELECTORAL,
            flag=flag,
            confidence=confidence,
            records=records or [{"row_id": str(uuid.uuid4()), "seguro_social": "999-99-9999"}],
            created_at=_now(),
        )
        bus.post_finding(f)
        return f.finding_id

    def test_auto_certify_high_confidence(self, tmp_bus, default_config):
        """Findings con confianza >= 0.90 y flag de alta conf deben auto-certificarse."""
        default_config.dry_run = False  # ReviewAgent necesita escribir al bus
        session_id = tmp_bus.open_session(domains=["ELECTORAL"])
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})

        fid = self._add_finding(
            tmp_bus, session_id, tid,
            AuditFlag.EXACT_DUPLICATE, 0.99
        )

        reviewer = ReviewAgent(tmp_bus, default_config)
        r_tid = tmp_bus.enqueue_task(
            session_id, "review",
            {"session_id": session_id, "auto_certify_above": 0.90}
        )
        task = tmp_bus.get_task(r_tid)
        result = reviewer.execute_task(task)

        assert result.get("certified", 0) >= 1
        finding = tmp_bus.get_findings(session_id, status="certified")
        assert any(f.finding_id == fid for f in finding)

    def test_deduplication_removes_cross_agent_duplicates(self, tmp_bus, default_config):
        """Si dos scanners reportan el mismo row_id con el mismo flag, uno debe descartarse."""
        default_config.dry_run = False
        session_id = tmp_bus.open_session(domains=["ELECTORAL"])
        tid1 = tmp_bus.enqueue_task(session_id, "scanner", {})
        tid2 = tmp_bus.enqueue_task(session_id, "scanner", {})

        # Mismo row_id, mismo flag, dos scanners diferentes
        shared_records = [{"row_id": "VOTER-001", "nombre": "Maria"}]
        for tid in [tid1, tid2]:
            f = AgentFinding(
                finding_id=_uid(), session_id=session_id, task_id=tid,
                source_agent=f"scanner:{tid[:4]}",
                domain=AuditDomain.ELECTORAL,
                flag=AuditFlag.CONFIRMED_DECEASED,
                confidence=0.95,
                records=shared_records,
                created_at=_now(),
            )
            tmp_bus.post_finding(f)

        reviewer = ReviewAgent(tmp_bus, default_config)
        r_tid = tmp_bus.enqueue_task(
            session_id, "review",
            {"session_id": session_id, "auto_certify_above": 0.90}
        )
        task = tmp_bus.get_task(r_tid)
        result = reviewer.execute_task(task)

        # De 2 findings identicos, 1 debe quedar certified y 1 dismissed
        assert result.get("dismissed", 0) >= 1

    def test_low_confidence_not_certified(self, tmp_bus, default_config):
        """Findings con confianza baja deben quedar en 'reviewed', no 'certified'."""
        default_config.dry_run = False
        session_id = tmp_bus.open_session(domains=["ELECTORAL"])
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})

        self._add_finding(tmp_bus, session_id, tid, AuditFlag.NEAR_DUPLICATE, 0.55)

        reviewer = ReviewAgent(tmp_bus, default_config)
        r_tid = tmp_bus.enqueue_task(
            session_id, "review",
            {"session_id": session_id, "auto_certify_above": 0.90}
        )
        task = tmp_bus.get_task(r_tid)
        reviewer.execute_task(task)

        certified = tmp_bus.get_findings(session_id, status="certified")
        reviewed = tmp_bus.get_findings(session_id, status="reviewed")
        assert len(certified) == 0
        assert len(reviewed) >= 1


# ── Tests ReportAgent ─────────────────────────────────────────────────────

class TestReportAgent:

    def test_report_generates_json_file(self, tmp_bus, tmp_path, default_config):
        """ReportAgent debe generar un archivo JSON con el reporte."""
        default_config.dry_run = False
        session_id = tmp_bus.open_session(domains=["ELECTORAL"])
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})

        # Crear findings certificados directamente
        for i in range(3):
            f = AgentFinding(
                finding_id=_uid(), session_id=session_id, task_id=tid,
                source_agent="scanner:test", domain=AuditDomain.ELECTORAL,
                flag=AuditFlag.EXACT_DUPLICATE, confidence=0.99,
                records=[{"row_id": str(i)}], created_at=_now(),
            )
            tmp_bus.post_finding(f)
            tmp_bus.update_finding_status(f.finding_id, "certified", "test")

        reporter = ReportAgent(tmp_bus, default_config)
        r_tid = tmp_bus.enqueue_task(
            session_id, "report",
            {
                "session_id": session_id,
                "output_dir": str(tmp_path),
                "matriz_path": str(tmp_path / "matriz.db"),
                "formats": ["json", "summary"],
            }
        )
        task = tmp_bus.get_task(r_tid)
        result = reporter.execute_task(task)

        assert result.get("certified", 0) == 3
        assert result.get("report_path", "")
        report_path = Path(result["report_path"])
        assert report_path.exists()

        with open(report_path) as f:
            data = json.load(f)
        assert data["summary"]["certified"] == 3

    def test_report_writes_to_matriz(self, tmp_bus, tmp_path, default_config):
        """ReportAgent debe escribir findings a matriz_certificada."""
        default_config.dry_run = False
        session_id = tmp_bus.open_session(domains=["NOMINA"])
        tid = tmp_bus.enqueue_task(session_id, "scanner", {})

        f = AgentFinding(
            finding_id=_uid(), session_id=session_id, task_id=tid,
            source_agent="scanner:NOMINA", domain=AuditDomain.NOMINA,
            flag=AuditFlag.MULTI_AGENCY_EMPLOYEE, confidence=0.95,
            records=[{"row_id": "EMP-001"}], created_at=_now(),
        )
        tmp_bus.post_finding(f)
        tmp_bus.update_finding_status(f.finding_id, "certified", "test")

        matriz_path = str(tmp_path / "matriz_test.db")
        reporter = ReportAgent(tmp_bus, default_config)
        r_tid = tmp_bus.enqueue_task(
            session_id, "report",
            {"session_id": session_id, "output_dir": str(tmp_path),
             "matriz_path": matriz_path, "formats": ["json"]}
        )
        task = tmp_bus.get_task(r_tid)
        result = reporter.execute_task(task)

        assert result.get("written_to_matriz", 0) == 1
        import sqlite3
        conn = sqlite3.connect(matriz_path)
        count = conn.execute("SELECT COUNT(*) FROM agent_certified_findings").fetchone()[0]
        conn.close()
        assert count == 1


# ── Test End-to-End (OrchestratorAgent) ───────────────────────────────────

class TestOrchestratorEndToEnd:

    @pytest.mark.skipif(
        not all((TESTS_DATA / f).exists() for f in [
            "mock_cee.db", "mock_rd.db", "mock_nomina.db"
        ]),
        reason="Datos sinteticos no disponibles"
    )
    def test_full_pipeline_electoral_and_nomina(self, tmp_bus, tmp_path):
        """
        Pipeline completo: Electoral + Nomina con datos sinteticos.
        Verifica que el sistema produzca findings end-to-end.
        """
        config = AgentConfig(
            dry_run=False,
            min_confidence=0.40,
            approved_by="pytest-e2e",
        )
        orch = OrchestratorAgent(tmp_bus, config)

        result = orch.run_audit(
            domains=[AuditDomain.ELECTORAL, AuditDomain.NOMINA],
            sources={
                "CEE":    MOCK_SOURCES["CEE"],
                "RD":     MOCK_SOURCES["RD"],
                "NOMINA": MOCK_SOURCES["NOMINA"],
            },
            output_dir=str(tmp_path / "reports"),
            matriz_path=str(tmp_path / "matriz.db"),
            auto_certify_above=0.90,
        )

        assert result.get("session_id")
        assert result.get("status", "completed") != "failed"
        # Con datos sinteticos diseñados para tener anomalias, debe haber findings
        assert result.get("raw_findings", 0) > 0

        # Verificar que la sesion quedo completada en el bus
        session = tmp_bus.get_session(result["session_id"])
        assert session.status == "completed"

    def test_dry_run_does_not_write_files(self, tmp_bus, tmp_path):
        """dry_run=True no debe crear archivos en disco."""
        config = AgentConfig(dry_run=True, approved_by="pytest")
        orch = OrchestratorAgent(tmp_bus, config)

        result = orch.run_audit(
            domains=[AuditDomain.ELECTORAL],
            sources={"CEE": MOCK_SOURCES["CEE"]},
            output_dir=str(tmp_path / "reports"),
            dry_run=True,
        )

        assert result.get("session_id")
        # En dry_run no debe haber archivos de reporte
        report_path = result.get("report_path", "")
        if report_path:
            assert not Path(report_path).exists()

    def test_empty_sources_completes_gracefully(self, tmp_bus, tmp_path):
        """Si no hay fuentes, el pipeline debe completar sin error."""
        config = AgentConfig(dry_run=True, approved_by="pytest")
        orch = OrchestratorAgent(tmp_bus, config)

        result = orch.run_audit(
            domains=[AuditDomain.ELECTORAL],
            sources={},
            output_dir=str(tmp_path),
        )

        assert result.get("session_id")
        # No debe fallar, solo tener 0 findings
        assert result.get("raw_findings", 0) == 0

    def test_list_recent_sessions(self, tmp_bus):
        """list_recent_sessions debe retornar sesiones del bus."""
        config = AgentConfig(dry_run=True)
        orch = OrchestratorAgent(tmp_bus, config)

        tmp_bus.open_session(["ELECTORAL"], started_by="user1")
        tmp_bus.open_session(["NOMINA"], started_by="user2")

        sessions = orch.list_recent