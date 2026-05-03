"""
scanner_agent.agents.orchestrator  v1.0.0

OrchestratorAgent — coordinador del pipeline multi-agente.

Responsabilidad:
    1. Abrir una sesion de auditoria en el AgentBus
    2. Encolar tareas de escaneo para cada dominio solicitado
    3. Ejecutar los DomainScannerAgents (secuencial o paralelo)
    4. Disparar el ReviewAgent cuando todos los scanners terminan
    5. Disparar el ReportAgent cuando el review termina
    6. Cerrar la sesion con el estado final
    7. Retornar el resumen completo

El orquestador es el unico agente que conoce a todos los demas.
Los DomainScannerAgents, ReviewAgent y ReportAgent no se conocen
entre si — solo hablan a traves del AgentBus.

Uso tipico (CLI o web):
    bus = AgentBus("reports/agent_bus.db")
    config = AgentConfig(approved_by="Jose Rodriguez", dry_run=False)
    orch = OrchestratorAgent(bus, config)

    result = orch.run_audit(
        domains=["ELECTORAL", "NOMINA"],
        sources={
            "CEE":   "sqlite:///tests/data/mock_cee.db",
            "RD":    "sqlite:///tests/data/mock_rd.db",
            "CESCO": "sqlite:///tests/data/mock_cesco.db",
            "NOMINA":"sqlite:///tests/data/mock_nomina.db",
        },
        output_dir="reports/",
        matriz_path="tests/data/matriz_certificada.db",
    )
    print(f"Sesion: {result['session_id']}")
    print(f"Certificados: {result['certified']}")
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from scanner_agent.agents.base_agent import BaseAgent, AgentConfig
from scanner_agent.agents.bus import AgentBus, AgentTask, _now
from scanner_agent.agents.scanner_agent import DomainScannerAgent
from scanner_agent.agents.review_agent import ReviewAgent
from scanner_agent.agents.report_agent import ReportAgent
from scanner_agent.models import AuditDomain

logger = logging.getLogger(__name__)

# Fuentes relevantes por dominio
_DOMAIN_SOURCES = {
    AuditDomain.ELECTORAL: ["CEE", "RD", "CESCO"],
    AuditDomain.NOMINA:    ["NOMINA", "RD"],   # RD para cruce de fallecidos
    AuditDomain.CONTRATOS: ["CONTRATOS"],
    AuditDomain.PENSIONES: ["PENSIONES"],
    AuditDomain.SALUD:     ["SALUD"],
}


class OrchestratorAgent(BaseAgent):
    """
    Agente orquestador del sistema de auditoria multi-agente.

    Coordina el pipeline completo:
        Scanner(s) → Review → Report
    """

    @property
    def agent_type(self) -> str:
        return "orchestrator"

    # ── API publica principal ─────────────────────────────────────────────

    def run_audit(
        self,
        domains: list[str],
        sources: dict[str, str],
        output_dir: str = "reports",
        matriz_path: str = "",
        staging_path: str = "",
        tables: Optional[dict[str, str]] = None,
        exclusions_path: str = "",
        auto_certify_above: float = 0.90,
        dry_run: bool = False,
        started_by: str = "CLI",
    ) -> dict[str, Any]:
        """
        Punto de entrada principal. Ejecuta la auditoria completa.

        Args:
            domains:    lista de AuditDomain a escanear
            sources:    dict alias → connection_string de cada fuente
            output_dir: directorio para reports JSON y summary
            matriz_path: path a la matriz_certificada.db
            staging_path: path al staging DB (usado en pipeline electoral)
            tables:     dict alias → nombre de tabla (opcional)
            exclusions_path: path a la DB de exclusiones
            auto_certify_above: confianza minima para auto-certificar
            dry_run:    si True, no escribe nada a disco ni a matriz
            started_by: nombre del usuario que aprueba la sesion

        Returns:
            dict con session_id, contadores y paths de output
        """
        if dry_run:
            self.config.dry_run = True

        # 1. Abrir sesion
        session_id = self.bus.open_session(
            domains=domains,
            started_by=started_by,
            notes=f"dry_run={dry_run} domains={domains}",
        )
        self.logger.info(
            "Auditoria iniciada: session=%s dominios=%s",
            session_id[:8], domains,
        )

        try:
            # 2. Fase de escaneo
            self.bus.update_session_status(session_id, "scanning")
            scan_results = self._run_scan_phase(
                session_id=session_id,
                domains=domains,
                sources=sources,
                tables=tables or {},
                staging_path=staging_path,
            )
            total_raw = sum(r.get("findings_count", 0) for r in scan_results)
            self.logger.info("Fase de escaneo completada: %d findings raw", total_raw)

            # 3. Fase de revision
            self.bus.update_session_status(session_id, "reviewing")
            review_result = self._run_review_phase(
                session_id=session_id,
                exclusions_path=exclusions_path,
                auto_certify_above=auto_certify_above,
            )
            self.logger.info(
                "Fase de revision completada: certified=%d reviewed=%d dismissed=%d",
                review_result.get("certified", 0),
                review_result.get("reviewed", 0),
                review_result.get("dismissed", 0),
            )

            # 4. Fase de reporte
            self.bus.update_session_status(session_id, "reporting")
            report_result = self._run_report_phase(
                session_id=session_id,
                output_dir=output_dir,
                matriz_path=matriz_path or str(Path(output_dir) / "matriz_certificada.db"),
                dry_run=dry_run,
            )

            # 5. Cerrar sesion
            self.bus.update_session_status(session_id, "completed")

            final = {
                "session_id": session_id,
                "domains": domains,
                "sources_scanned": list(sources.keys()),
                "raw_findings": total_raw,
                "certified": report_result.get("certified", 0),
                "reviewed_pending": report_result.get("reviewed", 0),
                "dismissed": report_result.get("dismissed", 0),
                "written_to_matriz": report_result.get("written_to_matriz", 0),
                "report_path": report_result.get("report_path", ""),
                "summary_path": report_result.get("summary_path", ""),
                "dry_run": dry_run,
            }

            self.logger.info(
                "Auditoria completada: session=%s certified=%d pending=%d",
                session_id[:8],
                final["certified"],
                final["reviewed_pending"],
            )
            return final

        except Exception as exc:
            self.logger.error(
                "Error en auditoria session=%s: %s", session_id[:8], exc, exc_info=True
            )
            self.bus.update_session_status(session_id, "failed")
            return {
                "session_id": session_id,
                "error": str(exc),
                "status": "failed",
            }

    # ── Fases ─────────────────────────────────────────────────────────────

    def _run_scan_phase(
        self,
        session_id: str,
        domains: list[str],
        sources: dict[str, str],
        tables: dict[str, str],
        staging_path: str,
    ) -> list[dict]:
        """
        Encola y ejecuta un DomainScannerAgent por cada dominio.
        Ejecuta secuencialmente (v1.0). En v2.0 se puede paralelizar con threading.
        """
        scanner_config = AgentConfig(
            dry_run=self.config.dry_run,
            min_confidence=self.config.min_confidence,
            approved_by=self.config.approved_by,
        )
        scanner = DomainScannerAgent(self.bus, scanner_config)
        scanner.setup()

        results = []
        for domain in domains:
            # Filtrar solo las fuentes relevantes para este dominio
            domain_sources = {
                alias: conn
                for alias, conn in sources.items()
                if alias in _DOMAIN_SOURCES.get(domain, [alias])
            }

            payload = {
                "domain": domain,
                "sources": domain_sources,
                "tables": tables,
                "staging_path": staging_path,
            }

            task_id = self.bus.enqueue_task(
                session_id=session_id,
                agent_type="scanner",
                payload=payload,
                domain=domain,
                priority=3,
            )

            task = self.bus.get_task(task_id)
            if task:
                result = scanner.execute_task(task)
                results.append(result)
            else:
                self.logger.error("No se pudo recuperar tarea %s", task_id[:8])

        scanner.teardown()
        return results

    def _run_review_phase(
        self,
        session_id: str,
        exclusions_path: str,
        auto_certify_above: float,
    ) -> dict:
        """Ejecuta el ReviewAgent sobre todos los findings de la sesion."""
        review_config = AgentConfig(
            dry_run=self.config.dry_run,
            min_confidence=self.config.min_confidence,
            approved_by=self.config.approved_by,
        )
        reviewer = ReviewAgent(self.bus, review_config)
        reviewer.setup()

        payload = {
            "session_id": session_id,
            "exclusions_path": exclusions_path,
            "auto_certify_above": auto_certify_above,
        }

        task_id = self.bus.enqueue_task(
            session_id=session_id,
            agent_type="review",
            payload=payload,
            priority=2,
        )

        task = self.bus.get_task(task_id)
        result = reviewer.execute_task(task) if task else {}
        reviewer.teardown()
        return result

    def _run_report_phase(
        self,
        session_id: str,
        output_dir: str,
        matriz_path: str,
        dry_run: bool,
    ) -> dict:
        """Ejecuta el ReportAgent para generar la salida final."""
        report_config = AgentConfig(
            dry_run=dry_run,
            approved_by=self.config.approved_by,
        )
        reporter = ReportAgent(self.bus, report_config)
        reporter.setup()

        payload = {
            "session_id": session_id,
            "output_dir": output_dir,
            "matriz_path": matriz_path,
            "formats": ["json", "summary"],
        }

        task_id = self.bus.enqueue_task(
            session_id=session_id,
            agent_type="report",
            payload=payload,
            priority=1,
        )

        task = self.bus.get_task(task_id)
        result = reporter.execute_task(task) if task else {}
        reporter.teardown()
        return result

    # ── run() requerido por BaseAgent ─────────────────────────────────────

    def run(self, task: AgentTask) -> dict[str, Any]:
        """
        Implementacion de BaseAgent.run() para cuando el orquestador
        recibe una tarea del bus (modo server / daemon futuro).
        """
        payload = task.payload
        return self.run_audit(
            domains=payload.get("domains", [AuditDomain.ELECTORAL]),
            sources=payload.get("sources", {}),
            output_dir=payload.get("output_dir", "reports"),
            matriz_path=payload.get("matriz_path", ""),
            staging_path=payload.get("staging_path", ""),
            exclusions_path=payload.get("exclusions_path", ""),
            auto_certify_above=payload.get("auto_certify_above", 0.90),
            dry_run=payload.get("dry_run", False),
            started_by=self.config.approved_by,
        )

    # ── Utilidades ────────────────────────────────────────────────────────

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        """Retorna el estado actual de una sesion para el dashboard."""
        return self.bus.session_stats(session_id)

    def list_recent_sessions(self, limit: int = 10) -> list[dict]:
        """Lista las sesiones mas recientes para el dashboard."""
        sessions = self.bus.list_sessions(limit=limit)
        return [
            {
                "session_id": s.session_id,
                "status": s.status,
                "domains": s.domains,
                "started_by": s.started_by,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "total_findings": s.total_findings,
                "certified_findings": s.certified_findings,
            }
            for s in sessions
        ]
