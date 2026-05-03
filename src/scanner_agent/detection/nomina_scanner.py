"""
scanner_agent.detection.nomina_scanner  v0.9.0

Motor de deteccion de anomalias en nominas de agencias de gobierno PR.

Detectores:
  scan_multi_agency()       -> MULTI_AGENCY_EMPLOYEE  (mismo SSN, >=2 agencias)
  scan_deceased_employees() -> DECEASED_EMPLOYEE      (cruce con RD defunciones)
  scan_salary_anomalies()   -> SALARY_ANOMALY         (outlier estadistico por categoria)
  run_nomina_scan()         -> orquestador completo

Uso:
    from scanner_agent.detection.nomina_scanner import run_nomina_scan
    results = run_nomina_scan(nomina_records, rd_records=defunciones)
"""

import logging
import statistics
from collections import defaultdict

from scanner_agent.models import AuditDomain, AuditFlag, RecordMatchGroup, RowRecord

logger = logging.getLogger(__name__)


# ── MULTI_AGENCY_EMPLOYEE ─────────────────────────────────────────────────

def scan_multi_agency(records: list[RowRecord]) -> list[RecordMatchGroup]:
    """
    Detecta empleados cuyo SSN aparece activo en 2 o mas agencias.

    Retorna un RecordMatchGroup por SSN conflictivo.
    La confianza es 1.0 (match exacto de SSN).
    """
    by_ssn: dict[str, list[RowRecord]] = defaultdict(list)
    for rec in records:
        if rec.seguro_social and rec.fecha_fin_empleo is None:   # solo activos
            by_ssn[rec.seguro_social].append(rec)

    groups: list[RecordMatchGroup] = []
    for ssn, recs in by_ssn.items():
        agencias = {r.agencia for r in recs if r.agencia}
        if len(agencias) >= 2:
            agencias_str = " | ".join(sorted(agencias))
            groups.append(RecordMatchGroup(
                strategy   = "ssn_multi_agency",
                confidence = 1.0,
                flag       = AuditFlag.MULTI_AGENCY_EMPLOYEE,
                records    = recs,
                notes      = (
                    f"SSN {ssn} aparece activo en {len(agencias)} agencias: {agencias_str}"
                ),
                domain     = AuditDomain.NOMINA,
            ))

    logger.info("multi_agency: %d grupos detectados", len(groups))
    return groups


# ── DECEASED_EMPLOYEE ─────────────────────────────────────────────────────

def scan_deceased_employees(
    nomina_records: list[RowRecord],
    rd_records: list[RowRecord],
) -> list[RecordMatchGroup]:
    """
    Cruza nomina_records contra rd_records (defunciones del Registro Demografico).
    Detecta empleados activos cuyo SSN aparece como fallecido.

    rd_records debe ser la lista de defunciones (source_db="RD").
    Confianza: 0.97 si coincide SSN exacto.
    """
    # Indice de SSNs fallecidos
    deceased_ssn: dict[str, RowRecord] = {}
    for rd in rd_records:
        if rd.seguro_social:
            deceased_ssn[rd.seguro_social] = rd

    groups: list[RecordMatchGroup] = []
    for rec in nomina_records:
        if not rec.seguro_social:
            continue
        if rec.fecha_fin_empleo is not None:   # ya no activo, menos urgente
            continue
        rd_match = deceased_ssn.get(rec.seguro_social)
        if rd_match:
            fecha_def = rd_match.fecha_transaccion or rd_match.fecha_nacimiento or "fecha desconocida"
            groups.append(RecordMatchGroup(
                strategy   = "ssn_cross_rd_defunciones",
                confidence = 0.97,
                flag       = AuditFlag.DECEASED_EMPLOYEE,
                records    = [rec, rd_match],
                notes      = (
                    f"{rec.nombre_completo} (SSN {rec.seguro_social}) "
                    f"aparece activo en nomina de {rec.agencia} "
                    f"pero consta fallecido en RD."
                ),
                domain     = AuditDomain.NOMINA,
            ))

    logger.info("deceased_employees: %d detectados", len(groups))
    return groups


# ── SALARY_ANOMALY ────────────────────────────────────────────────────────

def scan_salary_anomalies(
    records: list[RowRecord],
    zscore_threshold: float = 2.5,
) -> list[RecordMatchGroup]:
    """
    Detecta salarios estadisticamente anomalos dentro de cada categoria de puesto.

    Metodo: Z-score por categoria. Si |z| > zscore_threshold → SALARY_ANOMALY.
    Requiere al menos 3 registros por categoria para calcular estadisticas.

    Parametros:
        zscore_threshold : umbral de z-score (default 2.5 — ~1% de la distribucion)
    """
    by_cat: dict[str, list[RowRecord]] = defaultdict(list)
    for rec in records:
        cat = (rec.puesto or "SIN_CATEGORIA").upper().replace(" ", "_")
        if rec.salario is not None:
            by_cat[cat].append(rec)

    groups: list[RecordMatchGroup] = []

    for cat, recs in by_cat.items():
        if len(recs) < 3:
            continue
        salarios = [r.salario for r in recs]
        mean     = statistics.mean(salarios)
        stdev    = statistics.stdev(salarios)
        if stdev == 0:
            continue

        for rec in recs:
            z = (rec.salario - mean) / stdev
            if abs(z) > zscore_threshold:
                direction = "SUPERIOR" if z > 0 else "INFERIOR"
                groups.append(RecordMatchGroup(
                    strategy   = "salary_zscore",
                    confidence = round(min(0.99, 0.75 + (abs(z) - zscore_threshold) * 0.05), 2),
                    flag       = AuditFlag.SALARY_ANOMALY,
                    records    = [rec],
                    notes      = (
                        f"{rec.nombre_completo} ({rec.agencia}) — "
                        f"salario ${rec.salario:,.2f} es {direction} al esperado "
                        f"para '{cat}' (media ${mean:,.2f}, z={z:.2f})"
                    ),
                    domain     = AuditDomain.NOMINA,
                ))

    logger.info("salary_anomalies: %d detectados (umbral z=%.1f)", len(groups), zscore_threshold)
    return groups


# ── Orquestador ───────────────────────────────────────────────────────────

def run_nomina_scan(
    nomina_records: list[RowRecord],
    rd_records: list[RowRecord] | None = None,
    zscore_threshold: float = 2.5,
) -> dict:
    """
    Ejecuta todos los detectores de nomina y devuelve un reporte unificado.

    Parametros:
        nomina_records    : Registros de nomina cargados como RowRecord.
        rd_records        : Defunciones del Registro Demografico (opcional).
                            Si se proveen, activa scan_deceased_employees.
        zscore_threshold  : Umbral para deteccion de anomalias salariales.

    Retorna:
        {
          "total_records": int,
          "findings": list[dict],   # serializables a JSON
          "summary": {
              "MULTI_AGENCY_EMPLOYEE": int,
              "DECEASED_EMPLOYEE": int,
              "SALARY_ANOMALY": int,
              "total_flagged_records": int,
          }
        }
    """
    findings: list[RecordMatchGroup] = []

    findings += scan_multi_agency(nomina_records)

    if rd_records:
        findings += scan_deceased_employees(nomina_records, rd_records)

    findings += scan_salary_anomalies(nomina_records, zscore_threshold=zscore_threshold)

    # Contar registros unicos flaggeados
    flagged_ids: set[str] = set()
    for g in findings:
        for r in g.records:
            if r.source_domain == AuditDomain.NOMINA:
                flagged_ids.add(r.row_id)

    summary = {
        AuditFlag.MULTI_AGENCY_EMPLOYEE: sum(
            1 for f in findings if f.flag == AuditFlag.MULTI_AGENCY_EMPLOYEE),
        AuditFlag.DECEASED_EMPLOYEE: sum(
            1 for f in findings if f.flag == AuditFlag.DECEASED_EMPLOYEE),
        AuditFlag.SALARY_ANOMALY: sum(
            1 for f in findings if f.flag == AuditFlag.SALARY_ANOMALY),
        "total_flagged_records": len(flagged_ids),
    }

    logger.info(
        "nomina_scan completo | total=%d | flagged=%d | multi_agency=%d | deceased=%d | salary=%d",
        len(nomina_records),
        summary["total_flagged_records"],
        summary[AuditFlag.MULTI_AGENCY_EMPLOYEE],
        summary[AuditFlag.DECEASED_EMPLOYEE],
        summary[AuditFlag.SALARY_ANOMALY],
    )

    # Serializar findings a dicts
    def _serialize(g: RecordMatchGroup) -> dict:
        return {
            "flag":       g.flag,
            "strategy":   g.strategy,
            "confidence": g.confidence,
            "domain":     g.domain,
            "notes":      g.notes,
            "records": [
                {
                    "row_id":         r.row_id,
                    "nombre_completo": r.nombre_completo,
                    "seguro_social":  r.seguro_social,
                    "agencia":        r.agencia,
                    "salario":        r.salario,
                    "source_db":      r.source_db,
                }
                for r in g.records
            ],
        }

    return {
        "total_records": len(nomina_records),
        "findings":      [_serialize(g) for g in findings],
        "summary":       summary,
    }
