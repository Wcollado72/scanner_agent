"""
scanner_agent.agents.base_agent  v1.0.0

BaseAgent — clase base que todos los agentes heredan.

Ciclo de vida:
    agent = MyAgent(bus, config)
    agent.setup()       # inicializacion
    agent.run(task)     # ejecutar una tarea concreta
    agent.teardown()    # limpieza

Los agentes son stateless: todo el estado compartido vive en el AgentBus.
Cada agente solo conoce su propia responsabilidad (Single Responsibility).
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from scanner_agent.agents.bus import AgentBus, AgentTask, AgentFinding, _now, _uid

logger = logging.getLogger(__name__)


# ── Configuracion del agente ──────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Configuracion comun a todos los agentes."""
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    dry_run: bool = False           # si True, no escribe nada al bus ni a disco
    min_confidence: float = 0.40    # umbral minimo para publicar un finding
    approved_by: str = "CLI"        # quien aprobo la sesion
    output_dir: str = "reports"     # directorio base para outputs
    extra: dict[str, Any] = field(default_factory=dict)


# ── BaseAgent ─────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Clase base para todos los agentes del sistema.

    Subclases deben implementar:
        agent_type  (property str) — "scanner", "review", "report", "orchestrator"
        run(task)   — logica principal de la tarea
    """

    def __init__(self, bus: AgentBus, config: Optional[AgentConfig] = None):
        self.bus = bus
        self.config = config or AgentConfig()
        self.logger = logging.getLogger(
            f"scanner_agent.agents.{self.agent_type}.{self.config.agent_id}"
        )

    # ── Identidad ─────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Tipo de agente: 'scanner' | 'review' | 'report' | 'orchestrator'"""

    @property
    def agent_name(self) -> str:
        """Nombre legible para logs y mensajes."""
        return f"{self.agent_type}:{self.config.agent_id}"

    # ── Ciclo de vida ─────────────────────────────────────────────────────

    def setup(self) -> None:
        """Inicializacion antes de procesar tareas. Override si es necesario."""
        self.logger.info("Agente inicializado: %s", self.agent_name)

    @abstractmethod
    def run(self, task: AgentTask) -> dict[str, Any]:
        """
        Ejecuta la tarea y retorna un dict con el resultado.
        El resultado se guardara en agent_tasks.result via bus.complete_task().
        Lanzar excepcion en caso de fallo — BaseAgent lo captura.
        """

    def teardown(self) -> None:
        """Limpieza al terminar. Override si es necesario."""
        self.logger.info("Agente finalizado: %s", self.agent_name)

    # ── Ejecucion de tarea (con manejo de errores) ─────────────────────────

    def execute_task(self, task: AgentTask) -> dict[str, Any]:
        """
        Wrapper que reclama la tarea, corre run(), y reporta resultado al bus.
        Usar este metodo en lugar de llamar run() directamente.
        """
        if not self.bus.claim_task(task.task_id):
            self.logger.warning(
                "No se pudo reclamar tarea %s — puede que otro agente la tome",
                task.task_id[:8],
            )
            return {}

        self.logger.info(
            "Iniciando tarea: %s type=%s domain=%s",
            task.task_id[:8], self.agent_type, task.domain or "-",
        )

        try:
            result = self.run(task)
            self.bus.complete_task(task.task_id, result=result)
            self.bus.send_message(
                from_agent=self.agent_name,
                to_agent="orchestrator",
                msg_type="task_complete",
                payload={"task_id": task.task_id, "domain": task.domain},
                session_id=task.session_id,
            )
            self.logger.info(
                "Tarea completada: %s findings=%d",
                task.task_id[:8],
                result.get("findings_count", 0),
            )
            return result

        except Exception as exc:
            self.logger.error(
                "Error en tarea %s: %s", task.task_id[:8], exc, exc_info=True
            )
            self.bus.complete_task(
                task.task_id,
                result={},
                status="failed",
                error=str(exc),
            )
            self.bus.send_message(
                from_agent=self.agent_name,
                to_agent="orchestrator",
                msg_type="error",
                payload={"task_id": task.task_id, "error": str(exc)},
                session_id=task.session_id,
            )
            return {}

    # ── Helpers para publicar findings ────────────────────────────────────

    def publish_finding(
        self,
        session_id: str,
        task_id: str,
        domain: str,
        flag: str,
        confidence: float,
        records: list[dict],
        notes: str = "",
    ) -> Optional[str]:
        """
        Publica un finding al bus si supera el umbral de confianza.
        Retorna finding_id o None si fue descartado por umbral.
        """
        if confidence < self.config.min_confidence:
            self.logger.debug(
                "Finding descartado por umbral: flag=%s conf=%.2f < %.2f",
                flag, confidence, self.config.min_confidence,
            )
            return None

        finding = AgentFinding(
            finding_id=_uid(),
            session_id=session_id,
            task_id=task_id,
            source_agent=self.agent_name,
            domain=domain,
            flag=flag,
            confidence=confidence,
            records=records,
            notes=notes,
            created_at=_now(),
        )

        if self.config.dry_run:
            self.logger.info(
                "[DRY-RUN] Finding no publicado: flag=%s conf=%.2f records=%d",
                flag, confidence, len(records),
            )
            return finding.finding_id  # retornamos el ID de todos modos

        return self.bus.post_finding(finding)

    def _records_to_dicts(self, records: list) -> list[dict]:
        """
        Convierte una lista de RowRecord (o dicts) a lista de dicts serializables.
        Acepta tanto RowRecord dataclasses como dicts crudos.
        """
        result = []
        for r in records:
            if hasattr(r, "__dataclass_fields__"):
                # Es un dataclass (RowRecord)
                import dataclasses
                result.append(dataclasses.asdict(r))
            elif isinstance(r, dict):
                result.append(r)
            else:
                result.append({"raw": str(r)})
        return result
