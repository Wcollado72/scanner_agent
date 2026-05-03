"""
scanner_agent.agents  v1.0.0

Arquitectura multi-agente para auditoria de datos de gobierno PR.

Agentes disponibles:
    OrchestratorAgent  — abre sesion, coordina, consolida
    DomainScannerAgent — escanea un dominio (ELECTORAL, NOMINA, ...)
    ReviewAgent        — valida findings, filtra falsos positivos
    ReportAgent        — certifica y exporta resultados finales

Flujo:
    Orchestrator
        └─ encola tarea → ScannerAgent(ELECTORAL)
        └─ encola tarea → ScannerAgent(NOMINA)
        └─ espera findings → ReviewAgent
        └─ espera validados → ReportAgent
"""

from scanner_agent.agents.bus import AgentBus, AuditSession, AgentTask, AgentFinding
from scanner_agent.agents.base_agent import BaseAgent
from scanner_agent.agents.scanner_agent import DomainScannerAgent
from scanner_agent.agents.review_agent import ReviewAgent
from scanner_agent.agents.report_agent import ReportAgent
from scanner_agent.agents.orchestrator import OrchestratorAgent

__all__ = [
    "AgentBus",
    "AuditSession",
    "AgentTask",
    "AgentFinding",
    "BaseAgent",
    "DomainScannerAgent",
    "ReviewAgent",
    "ReportAgent",
    "OrchestratorAgent",
]
