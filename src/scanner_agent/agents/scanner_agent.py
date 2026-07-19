"""
scanner_agent.agents.scanner_agent  v1.0.0

DomainScannerAgent — agente de escaneo por dominio.

Responsabilidad:
    Recibe una tarea del bus con el dominio y las fuentes a escanear,
    corre el motor de deteccion correspondiente, y publica los findings
    al AgentBus para que el ReviewAgent los procese.

Dominios soportados:
    ELECTORAL  — CEE, RD, CESCO (4 fases)
    NOMINA     — nomina de gobierno PR
    CONTRATOS  — (stub, listo para implementar)
    PENSIONES  — (stub)
    SALUD      — (stub)

El agente NO escribe a ninguna base de datos de produccion.
Todo el output va al AgentBus (agent_findings).

Payload esperado en AgentTask:
    {
        "domain": "ELECTORAL",          # AuditDomain constant
        "sources": {                    # alias -> connection string
            "CEE":   "sqlite:///path/mock_cee.db",
            "RD":    "sqlite:///path/mock_rd.db",
            "CESCO": "sqlite:///path/mock_cesco.db"
        },
        "staging_path": "path/to/staging.db",   # para pipeline electoral
        "tables": {                     # opcional: tabla especifica por fuente
            "CEE": "electores"
        }
    }
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from scanner_agent.agents.base_agent import BaseAgent, AgentConfig
from scanner_agent.agents.bus import AgentBus, AgentTask
from scanner_agent.models import AuditDomain, AuditFlag

logger = logging.getLogger(__name__)

# Tablas por defecto para cada fuente conocida
_DEFAULT_TABLES: dict[str, str] = {
    "CEE":         "electores",
    "RD":          "defunciones",
    "CESCO":       "documentos",
    "NOMINA":      "empleados",
    "CONTRATOS":   "pagos",
    "PENSIONES":   "pensionados",
    "SALUD":       "reclamaciones",
}


class DomainScannerAgent(BaseAgent):
    """
    Agente de escaneo para un dominio especifico de gobierno.

    Un escaner por dominio: no mezcla logica de ELECTORAL con NOMINA, etc.
    Cuando lleguen fuentes reales, solo cambia el connection string en el payload.
    """

    @property
    def agent_type(self) -> str:
        return "scanner"

    def run(self, task: AgentTask) -> dict[str, Any]:
        payload = task.payload
        domain = payload.get("domain") or task.domain or AuditDomain.ELECTORAL

        self.logger.info("Escaneando dominio: %s", domain)

        if domain == AuditDomain.ELECTORAL:
            return self._run_electoral(task, payload)
        elif domain == AuditDomain.NOMINA:
            return self._run_nomina(task, payload)
        elif domain == AuditDomain.CONTRATOS:
            return self._run_stub(task, payload, domain)
        elif domain == AuditDomain.PENSIONES:
            return self._run_stub(task, payload, domain)
        elif domain == AuditDomain.SALUD:
            return self._run_stub(task, payload, domain)
        else:
            raise ValueError(f"Dominio no soportado: {domain}")

    # ── ELECTORAL ─────────────────────────────────────────────────────────

    def _run_electoral(self, task: AgentTask, payload: dict) -> dict[str, Any]:
        """
        Corre el pipeline electoral completo de 4 fases:
            Fase 1 — RD interno (duplicados dentro del Registro Demografico)
            Fase 2 — CEE interno (duplicados, clusters, fallecidos en padron)
            Fase 3 — RD x CEE  (cruce: fallecidos en padron, SSN conflicts)
            Fase 4 — staging x CESCO (validacion Real ID)
        """
        from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
        from scanner_agent.detection.record_matcher import run_full_match
        from scanner_agent.detection.cross_db_matcher import run_cross_db_match

        sources = payload.get("sources", {})
        tables  = payload.get("tables", {})
        staging = payload.get("staging_path", "")

        findings_count = 0

        # -- Fase 1: RD interno --
        if "RD" in sources:
            findings_count += self._scan_source_internal(
                task=task,
                domain=AuditDomain.ELECTORAL,
                alias="RD",
                conn_str=sources["RD"],
                table=tables.get("RD", _DEFAULT_TABLES["RD"]),
                field_map=_RD_FIELD_MAP,
                phase_label="RD-interno",
            )

        # -- Fase 2: CEE interno --
        if "CEE" in sources:
            findings_count += self._scan_source_internal(
                task=task,
                domain=AuditDomain.ELECTORAL,
                alias="CEE",
                conn_str=sources["CEE"],
                table=tables.get("CEE", _DEFAULT_TABLES["CEE"]),
                field_map=_CEE_FIELD_MAP,
                phase_label="CEE-interno",
            )

            # Analisis de clusters de direccion (solo CEE)
            findings_count += self._scan_address_clusters(
                task=task,
                alias="CEE",
                conn_str=sources["CEE"],
                table=tables.get("CEE", _DEFAULT_TABLES["CEE"]),
                field_map=_CEE_FIELD_MAP,
            )

        # -- Fase 3: RD x CEE cross-match --
        if "RD" in sources and "CEE" in sources:
            findings_count += self._scan_cross_electoral(
                task=task,
                sources=sources,
                tables=tables,
                staging=staging,
            )

        # -- Fase 4: staging x CESCO --
        if "CESCO" in sources and staging:
            findings_count += self._scan_cesco_validation(
                task=task,
                cesco_conn=sources["CESCO"],
                cesco_table=tables.get("CESCO", _DEFAULT_TABLES["CESCO"]),
                staging=staging,
            )

        return {
            "domain": AuditDomain.ELECTORAL,
            "findings_count": findings_count,
            "sources_scanned": list(sources.keys()),
        }

    def _scan_source_internal(
        self,
        task: AgentTask,
        domain: str,
        alias: str,
        conn_str: str,
        table: str,
        field_map: dict,
        phase_label: str,
    ) -> int:
        """Escaneo interno de una sola fuente: duplicados, anomalias de edad, etc."""
        from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
        from scanner_agent.detection.record_matcher import run_full_match

        self.logger.info("  [%s] Escaneando %s.%s", phase_label, alias, table)

        try:
            cfg = DbScanConfig(
                connection_string=conn_str,
                table=table,
                field_map=field_map,
                source_alias=alias,
            )
            rows = scan_db_table(cfg)
            if domain != AuditDomain.ELECTORAL:
                for rec in rows:
                    object.__setattr__(rec, "source_domain", domain)
            self.logger.info("  [%s] %d registros cargados", phase_label, len(rows))

            groups = run_full_match(rows)
            count = 0
            for grp in groups:
                finding_id = self.publish_finding(
                    session_id=task.session_id,
                    task_id=task.task_id,
                    domain=domain,
                    flag=grp.flag,
                    confidence=grp.confidence,
                    records=self._records_to_dicts(grp.records),
                    notes=f"[{phase_label}] {grp.notes}",
                )
                if finding_id:
                    count += 1

            self.logger.info("  [%s] %d findings publicados", phase_label, count)
            return count

        except Exception as exc:
            self.logger.error("  [%s] Error escaneando %s: %s", phase_label, alias, exc)
            return 0

    def _scan_address_clusters(
        self,
        task: AgentTask,
        alias: str,
        conn_str: str,
        table: str,
        field_map: dict,
    ) -> int:
        """Detecta clusters anomalos de direccion en el padron electoral."""
        from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
        from scanner_agent.detection.address_analyzer import find_address_clusters

        self.logger.info("  [address-clusters] Analizando clusters en %s", alias)

        try:
            cfg = DbScanConfig(
                connection_string=conn_str,
                table=table,
                field_map=field_map,
                source_alias=alias,
            )
            rows = scan_db_table(cfg)
            groups = find_address_clusters(rows)
            count = 0
            for grp in groups:
                finding_id = self.publish_finding(
                    session_id=task.session_id,
                    task_id=task.task_id,
                    domain=AuditDomain.ELECTORAL,
                    flag=AuditFlag.ADDRESS_CLUSTER,
                    confidence=grp.confidence,
                    records=self._records_to_dicts(grp.records),
                    notes=f"[address-clusters] {grp.notes}",
                )
                if finding_id:
                    count += 1
            self.logger.info("  [address-clusters] %d clusters publicados", count)
            return count
        except Exception as exc:
            self.logger.error("  [address-clusters] Error: %s", exc)
            return 0

    def _scan_cross_electoral(
        self,
        task: AgentTask,
        sources: dict,
        tables: dict,
        staging: str,
    ) -> int:
        """Cruce RD x CEE: fallecidos en padron, conflictos SSN."""
        from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
        from scanner_agent.detection.cross_db_matcher import run_cross_db_match

        self.logger.info("  [cross-electoral] Cruce RD x CEE")

        try:
            rd_cfg = DbScanConfig(
                connection_string=sources["RD"],
                table=tables.get("RD", _DEFAULT_TABLES["RD"]),
                field_map=_RD_FIELD_MAP,
                source_alias="RD",
            )
            cee_cfg = DbScanConfig(
                connection_string=sources["CEE"],
                table=tables.get("CEE", _DEFAULT_TABLES["CEE"]),
                field_map=_CEE_FIELD_MAP,
                source_alias="CEE",
            )
            rd_rows  = scan_db_table(rd_cfg)
            cee_rows = scan_db_table(cee_cfg)

            groups = run_cross_db_match(rd_rows, cee_rows)
            count = 0
            for grp in groups:
                finding_id = self.publish_finding(
                    session_id=task.session_id,
                    task_id=task.task_id,
                    domain=AuditDomain.ELECTORAL,
                    flag=grp.flag,
                    confidence=grp.confidence,
                    records=self._records_to_dicts(grp.records),
                    notes=f"[cross-RD-CEE] {grp.notes}",
                )
                if finding_id:
                    count += 1
            self.logger.info("  [cross-electoral] %d findings publicados", count)
            return count
        except Exception as exc:
            self.logger.error("  [cross-electoral] Error: %s", exc)
            return 0

    def _scan_cesco_validation(
        self,
        task: AgentTask,
        cesco_conn: str,
        cesco_table: str,
        staging: str,
    ) -> int:
        """Fase 4: validacion de staging contra CESCO Real ID."""
        # Por ahora retorna 0 — la logica de staging x CESCO se implementara
        # cuando tengamos el esquema de staging consolidado.
        self.logger.info("  [cesco-validation] Fase 4 pendiente de implementacion completa")
        return 0

    # ── NOMINA ────────────────────────────────────────────────────────────

    def _run_nomina(self, task: AgentTask, payload: dict) -> dict[str, Any]:
        """
        Escaneo del dominio Nomina:
            - Empleados en multiples agencias (MULTI_AGENCY_EMPLOYEE)
            - Empleados fallecidos activos (DECEASED_EMPLOYEE)
            - Anomalias salariales (SALARY_ANOMALY)
        """
        from scanner_agent.scanners.db_scanner import DbScanConfig, scan_db_table
        from scanner_agent.detection.nomina_scanner import run_nomina_scan

        sources = payload.get("sources", {})
        tables  = payload.get("tables", {})

        if "NOMINA" not in sources:
            self.logger.warning("No se especifico fuente NOMINA en el payload")
            return {"domain": AuditDomain.NOMINA, "findings_count": 0}

        self.logger.info("  [nomina] Escaneando nomina de gobierno")

        try:
            cfg = DbScanConfig(
                connection_string=sources["NOMINA"],
                table=tables.get("NOMINA", _DEFAULT_TABLES["NOMINA"]),
                field_map=_NOMINA_FIELD_MAP,
                source_alias="NOMINA",
            )
            rows = scan_db_table(cfg)
            for rec in rows:
                object.__setattr__(rec, "source_domain", AuditDomain.NOMINA)
            self.logger.info("  [nomina] %d registros cargados", len(rows))

            # run_nomina_scan retorna lista de RecordMatchGroup
            rd_rows_for_cross = []
            if "RD" in sources:
                rd_cfg = DbScanConfig(
                    connection_string=sources["RD"],
                    table=tables.get("RD", _DEFAULT_TABLES["RD"]),
                    field_map=_RD_FIELD_MAP,
                    source_alias="RD",
                )
                rd_rows_for_cross = scan_db_table(rd_cfg)

            groups = run_nomina_scan(rows, rd_rows=rd_rows_for_cross)
            count = 0
            for grp in groups:
                finding_id = self.publish_finding(
                    session_id=task.session_id,
                    task_id=task.task_id,
                    domain=AuditDomain.NOMINA,
                    flag=grp.flag,
                    confidence=grp.confidence,
                    records=self._records_to_dicts(grp.records),
                    notes=f"[nomina] {grp.notes}",
                )
                if finding_id:
                    count += 1

            self.logger.info("  [nomina] %d findings publicados", count)
            return {"domain": AuditDomain.NOMINA, "findings_count": count}

        except Exception as exc:
            self.logger.error("  [nomina] Error: %s", exc)
            return {"domain": AuditDomain.NOMINA, "findings_count": 0, "error": str(exc)}

    # ── Stubs para dominios futuros ───────────────────────────────────────

    def _run_stub(self, task: AgentTask, payload: dict, domain: str) -> dict[str, Any]:
        """
        Placeholder para dominios no implementados aun.
        Cuando llegue el momento, sustituir por logica real.
        """
        self.logger.info(
            "  [%s] Scanner stub — dominio pendiente de implementacion", domain
        )
        return {
            "domain": domain,
            "findings_count": 0,
            "status": "stub",
            "message": f"Scanner para {domain} pendiente de implementacion",
        }


# ── Field maps por fuente ─────────────────────────────────────────────────

_CEE_FIELD_MAP = {
    "row_id":           "voter_id",
    "nombre":           "nombre",
    "apellido_paterno": "apellido1",
    "apellido_materno": "apellido2",
    "fecha_nacimiento": "fecha_nac",
    "genero":           "genero",
    "municipio":        "municipio",
    "seguro_social":    "ssn",
    "direccion":        "direccion",
}

_RD_FIELD_MAP = {
    "row_id":           "cert_id",
    "nombre":           "nombre",
    "apellido_paterno": "apellido1",
    "apellido_materno": "apellido2",
    "fecha_nacimiento": "fecha_nac",
    "genero":           "genero",
    "municipio":        "municipio",
    "seguro_social":    "ssn",
}

_NOMINA_FIELD_MAP = {
    "row_id":               "emp_id",
    "nombre":               "nombre",
    "apellido_paterno":     "apellido_paterno",
    "apellido_materno":     "apellido_materno",
    "fecha_nacimiento":     "fecha_nacimiento",
    "genero":               "genero",
    "municipio":            "municipio",
    "seguro_social":        "seguro_social",
    "agencia":              "agencia",
    "puesto":               "puesto",
    "salario":              "salario",
    "fecha_inicio_empleo":  "fecha_inicio",
    "fecha_fin_empleo":     "fecha_fin",
}
