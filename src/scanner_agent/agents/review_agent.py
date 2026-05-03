"""
scanner_agent.agents.review_agent  v1.0.0

ReviewAgent — agente de revision y validacion de findings.

Responsabilidad:
    1. Tomar los findings publicados por los DomainScannerAgents
    2. Deduplicar findings que se refieren a la misma persona/registro
       detectada por multiples scanners
    3. Aplicar exclusion list (personas/entidades excluidas del reporte)
    4. Filtrar falsos positivos obvios (umbrales, reglas de negocio)
    5. Marcar findings como 'reviewed' (listos para ReportAgent)
       o 'dismissed' (descartados con razon documentada)

Este agente NO modifica los datos originales.
Solo actualiza el status de los findings en el AgentBus.

Payload esperado en AgentTask:
    {
        "session_id": "...",
        "min_confidence": 0.40,         # opcional, default del config
        "exclusions_path": "...",        # opcional, path a exclusions DB
        "auto_certify_above": 0.95,     # findings >= conf -> certified automaticamente
        "require_human_above": 0.0,     # si > 0, findings sobre este umbral esperan humano
    }
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

from scanner_agent.agents.base_agent import BaseAgent, AgentConfig
from scanner_agent.agents.bus import AgentBus, AgentTask, AgentFinding
from scanner_agent.models import AuditFlag

logger = logging.getLogger(__name__)

# Flags que por definicion tienen confianza alta y se pueden auto-certificar
_HIGH_CONFIDENCE_FLAGS = {
    AuditFlag.EXACT_DUPLICATE,
    AuditFlag.CONFIRMED_DECEASED,
    AuditFlag.MULTI_AGENCY_EMPLOYEE,
    AuditFlag.DECEASED_EMPLOYEE,
}

# Flags que siempre requieren revision humana antes de certificar
_HUMAN_REVIEW_FLAGS = {
    AuditFlag.NEAR_DUPLICATE,
    AuditFlag.NAME_MISMATCH_CROSS_DB,
    AuditFlag.POSSIBLE_DECEASED,
    AuditFlag.GHOST_EMPLOYEE,
}


class ReviewAgent(BaseAgent):
    """
    Agente de revision de hallazgos.

    Valida, deduplica y clasifica los findings de los scanners.
    Produce findings 'reviewed' o 'certified' listos para el ReportAgent.
    """

    @property
    def agent_type(self) -> str:
        return "review"

    def run(self, task: AgentTask) -> dict[str, Any]:
        payload  = task.payload
        session_id = payload.get("session_id") or task.session_id

        min_conf       = payload.get("min_confidence", self.config.min_confidence)
        auto_certify   = payload.get("auto_certify_above", 0.90)
        exclusions_path = payload.get("exclusions_path", "")

        self.logger.info(
            "ReviewAgent iniciando sesion=%s min_conf=%.2f auto_certify=%.2f",
            session_id[:8], min_conf, auto_certify,
        )

        # 1. Cargar todos los findings pendientes de esta sesion
        findings = self.bus.get_findings(
            session_id=session_id,
            status="pending",
            min_confidence=min_conf,
        )
        self.logger.info("Findings pendientes a revisar: %d", len(findings))

        if not findings:
            return {"session_id": session_id, "reviewed": 0, "certified": 0, "dismissed": 0}

        # 2. Cargar exclusiones si se proporcionaron
        exclusions = self._load_exclusions(exclusions_path)

        # 3. Deduplicar findings cross-dominio (misma persona, multiples scanners)
        deduplicated = self._deduplicate(findings)
        dismissed_duplicates = len(findings) - len(deduplicated)
        self.logger.info(
            "Deduplicacion: %d → %d (%d duplicados cross-agente)",
            len(findings), len(deduplicated), dismissed_duplicates,
        )

        # 4. Revisar cada finding
        reviewed = certified = dismissed = 0

        for f in deduplicated:
            decision, reason = self._review_finding(f, exclusions, auto_certify)

            self.bus.update_finding_status(
                finding_id=f.finding_id,
                status=decision,
                reviewed_by=self.agent_name,
                dismissed_reason=reason if decision == "dismissed" else "",
            )

            if decision == "certified":
                certified += 1
            elif decision == "reviewed":
                reviewed += 1
            elif decision == "dismissed":
                dismissed += 1

        # 5. Marcar duplicados cross-agente como dismissed
        for f in findings:
            if f not in deduplicated:
                self.bus.update_finding_status(
                    finding_id=f.finding_id,
                    status="dismissed",
                    reviewed_by=self.agent_name,
                    dismissed_reason="Duplicado cross-agente (mismo hallazgo detectado por multiples scanners)",
                )
                dismissed += 1

        result = {
            "session_id": session_id,
            "findings_input": len(findings),
            "reviewed": reviewed,
            "certified": certified,
            "dismissed": dismissed,
        }
        self.logger.info(
            "Review completado: reviewed=%d certified=%d dismissed=%d",
            reviewed, certified, dismissed,
        )
        return result

    # ── Logica de revision ────────────────────────────────────────────────

    def _review_finding(
        self,
        finding: AgentFinding,
        exclusions: set[str],
        auto_certify_above: float,
    ) -> tuple[str, str]:
        """
        Decide el estado de un finding.
        Retorna (status, razon_si_dismissed).

        Reglas (en orden de prioridad):
            1. Si alguna persona esta en la lista de exclusiones → dismissed
            2. Si confidence >= auto_certify_above y es un flag de alta conf → certified
            3. Si el flag requiere revision humana → reviewed (en espera)
            4. Si confidence >= 0.70 → reviewed
            5. Default → reviewed
        """
        # Regla 1: exclusiones
        for record in finding.records:
            ssn = record.get("seguro_social", "")
            row_id = str(record.get("row_id", ""))
            if ssn and ssn in exclusions:
                return "dismissed", f"SSN en lista de exclusiones: {ssn}"
            if row_id and row_id in exclusions:
                return "dismissed", f"ID en lista de exclusiones: {row_id}"

        # Regla 2: auto-certificacion
        if (
            finding.confidence >= auto_certify_above
            and finding.flag in _HIGH_CONFIDENCE_FLAGS
        ):
            return "certified", ""

        # Regla 3: flags que requieren humano
        if finding.flag in _HUMAN_REVIEW_FLAGS:
            return "reviewed", ""  # queda en 'reviewed' esperando auditor humano

        # Regla 4: confianza alta
        if finding.confidence >= 0.70:
            return "reviewed", ""

        # Default
        return "reviewed", ""

    def _deduplicate(self, findings: list[AgentFinding]) -> list[AgentFinding]:
        """
        Elimina findings duplicados que se refieren al mismo grupo de registros
        pero fueron detectados por multiples scanners o dominios.

        Estrategia: agrupar por (flag, frozenset de row_ids).
        Dentro del grupo, conservar el de mayor confianza.
        """
        # Clave: (flag, frozenset de row_ids en el finding)
        groups: dict[tuple, list[AgentFinding]] = defaultdict(list)

        for f in findings:
            row_ids = frozenset(
                str(r.get("row_id", r.get("num_empleado", i)))
                for i, r in enumerate(f.records)
            )
            key = (f.flag, row_ids)
            groups[key].append(f)

        result = []
        for key, group in groups.items():
            # Conservar el de mayor confianza
            best = max(group, key=lambda x: x.confidence)
            result.append(best)

        return result

    def _load_exclusions(self, exclusions_path: str) -> set[str]:
        """
        Carga la lista de exclusiones (SSNs / IDs excluidos del reporte).
        Retorna set vacio si no hay archivo o hay error.
        """
        if not exclusions_path:
            return set()

        try:
            from scanner_agent.safety.exclusions import load_exclusion_set
            return load_exclusion_set(exclusions_path)
        except Exception as exc:
            self.logger.warning("No se pudo cargar exclusiones de %s: %s", exclusions_path, exc)
            return set()
