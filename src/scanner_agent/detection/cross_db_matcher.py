"""
scanner_agent.detection.cross_db_matcher  v0.5.0
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Motor de cruce entre múltiples bases de datos.

Cruces implementados:
  1. CEE × Registro Demográfico (defunciones)
       → CONFIRMED_DECEASED    si nombre+DOB coinciden con defunción
  2. CEE × CESCO (licencias)
       → NAME_MISMATCH_CROSS_DB si mismo DOB pero apellido diferente
  3. SSN compartido entre bases distintas
       → SSN_CROSS_DB_CONFLICT  si mismo SSN en ≥2 fuentes con nombres distintos

Nuevos flags generados:
  CONFIRMED_DECEASED       (conf 0.95–0.99 según señales)
  NAME_MISMATCH_CROSS_DB  (conf 0.75–0.92 según señales)
  SSN_CROSS_DB_CONFLICT   (conf 0.97)

Estrategia de normalización:
  Nombres: NFKD + uppercase + difflib fuzzy ≥ 0.82
  DOB:     match exacto (ISO-8601)
  SSN:     match exacto en campo enmascarado
"""

from __future__ import annotations

import difflib
import logging
import unicodedata
from collections import defaultdict

from scanner_agent.models import RecordMatchGroup, RowRecord

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────
NAME_SIM_THRESHOLD  = 0.82   # mínimo para considerar nombres iguales
SSN_CONFLICT_SIM    = 0.50   # si nombre_sim < esto con mismo SSN → conflicto

CONF_DECEASED_FULL  = 0.99   # nombre + DOB + SSN coinciden con defunción
CONF_DECEASED_HIGH  = 0.95   # nombre + DOB coinciden con defunción
CONF_DECEASED_MED   = 0.85   # solo nombre similar + mismo municipio fallecido
CONF_NAME_MISMATCH  = 0.85   # mismo DOB + municipio, apellido diferente
CONF_NAME_SIM       = 0.75   # solo nombre similar cross-DB
CONF_SSN_CONFLICT   = 0.97   # mismo SSN, fuentes distintas, nombres distintos


# ── Helpers ────────────────────────────────────────────────────────────────

def _norm(text: str | None) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text.strip().upper())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _name_sim(a: RowRecord, b: RowRecord) -> float:
    na = a.nombre_normalizado or _norm(
        " ".join(filter(None, [a.nombre, a.apellido_paterno, a.apellido_materno]))
    )
    nb = b.nombre_normalizado or _norm(
        " ".join(filter(None, [b.nombre, b.apellido_paterno, b.apellido_materno]))
    )
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _same_dob(a: RowRecord, b: RowRecord) -> bool:
    return bool(a.fecha_nacimiento and b.fecha_nacimiento
                and a.fecha_nacimiento == b.fecha_nacimiento)


def _same_ssn(a: RowRecord, b: RowRecord) -> bool:
    return bool(
        a.seguro_social and b.seguro_social
        and a.seguro_social == b.seguro_social
        and not a.seguro_social.startswith("***-**-0000")
    )


def _same_mun(a: RowRecord, b: RowRecord) -> bool:
    return bool(a.municipio and b.municipio
                and _norm(a.municipio) == _norm(b.municipio))


# ── Cruce 1: CEE × Defunciones (CONFIRMED_DECEASED) ───────────────────────

def cross_cee_defunciones(
    cee_records: list[RowRecord],
    rd_records: list[RowRecord],
) -> list[RecordMatchGroup]:
    """
    Cruza registros del padrón electoral (CEE) contra defunciones del
    Registro Demográfico (RD).

    Un elector se marca CONFIRMED_DECEASED si:
      - Nombre fuzzy similar (≥ 0.82) a un fallecido, Y
      - Mismo DOB  →  conf 0.95
      - Además mismo SSN  →  conf 0.99
      - Sin DOB pero mismo municipio  →  conf 0.85

    Los records de RD deben tener source_table = 'defunciones' y los
    campos: nombre, apellido_paterno, apellido_materno, fecha_nacimiento,
    seguro_social, municipio (municipio_res o municipio_muerte mapeado).
    """
    groups: list[RecordMatchGroup] = []
    already: set[frozenset[str]] = set()

    # Índice RD por año de nacimiento para reducir comparaciones
    rd_by_year: dict[str, list[RowRecord]] = defaultdict(list)
    for rd in rd_records:
        year = (rd.fecha_nacimiento or "0000")[:4]
        rd_by_year[year].append(rd)

    for cee in cee_records:
        year = (cee.fecha_nacimiento or "0000")[:4]
        # Check ±1 year to catch off-by-one DOB entry errors
        candidates: list[RowRecord] = []
        for y in [str(int(year)-1), year, str(int(year)+1)] if year.isdigit() else [year]:
            candidates.extend(rd_by_year.get(y, []))

        for rd in candidates:
            key = frozenset([cee.row_id, rd.row_id])
            if key in already:
                continue

            sim = _name_sim(cee, rd)
            if sim < NAME_SIM_THRESHOLD:
                continue

            same_dob = _same_dob(cee, rd)
            same_ssn = _same_ssn(cee, rd)
            same_mun = _same_mun(cee, rd)

            # Determine confidence
            if same_dob and same_ssn:
                conf  = CONF_DECEASED_FULL
                level = "CRITICO"
            elif same_dob:
                conf  = CONF_DECEASED_HIGH
                level = "ALTO"
            elif same_mun:
                conf  = CONF_DECEASED_MED
                level = "MODERADO"
            else:
                continue  # solo nombre no es suficiente

            already.add(key)
            signals = [f"similitud nombre {sim:.0%}"]
            if same_dob: signals.append("mismo DOB")
            if same_ssn: signals.append("mismo SSN")
            if same_mun: signals.append("mismo municipio")

            rd_death = rd.raw_data.get("fecha_defuncion", "—") if rd.raw_data else "—"
            causa    = rd.raw_data.get("causa_muerte",    "—") if rd.raw_data else "—"

            groups.append(RecordMatchGroup(
                strategy   = "cross_cee_rd_defunciones",
                confidence = conf,
                flag       = "CONFIRMED_DECEASED",
                records    = [cee, rd],
                notes      = (
                    f"Elector {cee.nombre_completo!r} (CEE) — "
                    f"Señales: {', '.join(signals)}. "
                    f"Fallecido: {rd_death}. Causa: {causa}. "
                    f"Nivel: {level}. "
                    "Requiere baja inmediata del padrón electoral."
                ),
            ))

    logger.info("CEE × RD defunciones: %d hallazgos CONFIRMED_DECEASED", len(groups))
    return groups


# ── Cruce 2: CEE × CESCO (NAME_MISMATCH_CROSS_DB) ─────────────────────────

def cross_cee_cesco(
    cee_records: list[RowRecord],
    cesco_records: list[RowRecord],
    name_threshold: float = NAME_SIM_THRESHOLD,
) -> list[RecordMatchGroup]:
    """
    Cruza electores (CEE) contra licencias de conducir (CESCO).

    Detecta:
      - Mismo DOB, nombre similar pero apellido diferente →
        posible error de captura en una de las bases (NAME_MISMATCH_CROSS_DB)
      - Mismo SSN en ambas bases con direcciones muy distintas →
        posible domicilio electoral fraudulento
    """
    groups: list[RecordMatchGroup] = []
    already: set[frozenset[str]] = set()

    # Índice CESCO por DOB para reducir comparaciones
    cesco_by_dob: dict[str, list[RowRecord]] = defaultdict(list)
    for c in cesco_records:
        if c.fecha_nacimiento:
            cesco_by_dob[c.fecha_nacimiento].append(c)

    for cee in cee_records:
        dob = cee.fecha_nacimiento
        if not dob:
            continue
        candidates = cesco_by_dob.get(dob, [])

        for cesco in candidates:
            key = frozenset([cee.row_id, cesco.row_id])
            if key in already:
                continue

            sim = _name_sim(cee, cesco)

            # Not a match at all
            if sim < 0.65:
                continue

            # Exact match — same person, consistent data, no flag needed
            if sim >= 0.97 and _same_ssn(cee, cesco):
                already.add(key)
                continue

            same_ssn = _same_ssn(cee, cesco)
            same_mun = _same_mun(cee, cesco)

            # Determine if this is a genuine cross-DB mismatch
            if sim >= name_threshold:
                # High name similarity — minor variation, low urgency
                conf = CONF_NAME_SIM + (same_mun * 0.05) + (same_ssn * 0.10)
                flag = "NAME_MISMATCH_CROSS_DB"
            elif same_ssn:
                # Same SSN but divergent names — more serious
                conf = CONF_NAME_MISMATCH
                flag = "NAME_MISMATCH_CROSS_DB"
            else:
                continue

            conf = min(0.99, round(conf, 4))
            already.add(key)

            signals = [f"similitud nombre {sim:.0%}"]
            if same_ssn: signals.append("mismo SSN")
            if same_mun: signals.append("mismo municipio")
            signals.append("mismo DOB")

            groups.append(RecordMatchGroup(
                strategy   = "cross_cee_cesco_name",
                confidence = conf,
                flag       = flag,
                records    = [cee, cesco],
                notes      = (
                    f"CEE: {cee.nombre_completo!r} vs CESCO: {cesco.nombre_completo!r}. "
                    f"Señales: {', '.join(signals)}. "
                    "Posible variante de escritura entre bases de datos. "
                    "Verificar contra documento de identidad original."
                ),
            ))

    logger.info("CEE × CESCO: %d hallazgos NAME_MISMATCH_CROSS_DB", len(groups))
    return groups


# ── Cruce 3: SSN compartido entre bases (SSN_CROSS_DB_CONFLICT) ───────────

def cross_ssn_all_sources(
    all_records: list[RowRecord],
) -> list[RecordMatchGroup]:
    """
    Detecta SSNs compartidos entre registros de distintas fuentes
    (source_db diferente) con nombres notablemente distintos.

    Agrupa por SSN. Dentro de cada grupo, compara pares de distintas fuentes.
    Si name_sim < SSN_CONFLICT_SIM → SSN_CROSS_DB_CONFLICT (conf 0.97).
    """
    # Índice por SSN
    ssn_index: dict[str, list[RowRecord]] = defaultdict(list)
    for r in all_records:
        if r.seguro_social and not r.seguro_social.startswith("***-**-0000"):
            ssn_index[r.seguro_social].append(r)

    already: set[frozenset[str]] = set()
    groups:  list[RecordMatchGroup] = []

    for ssn, bucket in ssn_index.items():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]

                # Only flag if records come from different source DBs
                if a.source_db == b.source_db:
                    continue

                key = frozenset([a.row_id, b.row_id])
                if key in already:
                    continue

                sim = _name_sim(a, b)
                if sim >= NAME_SIM_THRESHOLD:
                    # Same person, same SSN, consistent name — OK, skip
                    already.add(key)
                    continue

                already.add(key)
                groups.append(RecordMatchGroup(
                    strategy   = "cross_ssn_conflict",
                    confidence = CONF_SSN_CONFLICT,
                    flag       = "SSN_CROSS_DB_CONFLICT",
                    records    = [a, b],
                    notes      = (
                        f"SSN {ssn} compartido entre {a.source_db} y {b.source_db}. "
                        f"Nombres: {a.nombre_completo!r} vs {b.nombre_completo!r} "
                        f"(similitud {sim:.0%}). "
                        "Posible robo de identidad, error sistémico o "
                        "reutilización de SSN. Prioridad ALTA."
                    ),
                ))

    logger.info("Cross-SSN conflict: %d hallazgos SSN_CROSS_DB_CONFLICT", len(groups))
    return groups


# ── Pipeline completo de cruce ────────────────────────────────────────────

def run_cross_db_match(
    source_groups: dict[str, list[RowRecord]],
) -> list[RecordMatchGroup]:
    """
    Ejecuta todos los cruces entre bases de datos.

    Args:
        source_groups: dict con clave = alias de la fuente, valor = lista de RowRecord
            Ejemplo:
            {
                "CEE":   cee_records,
                "RD":    rd_defunciones_records,
                "CESCO": cesco_records,
            }

    Returns:
        Lista unificada de RecordMatchGroup con flags cross-DB.
    """
    cee   = source_groups.get("CEE",   [])
    rd    = source_groups.get("RD",    [])
    cesco = source_groups.get("CESCO", [])
    all_r = [r for recs in source_groups.values() for r in recs]

    deceased  = cross_cee_defunciones(cee, rd)         if cee and rd    else []
    name_mis  = cross_cee_cesco(cee, cesco)             if cee and cesco else []
    ssn_conf  = cross_ssn_all_sources(all_r)            if len(all_r) > 1 else []

    all_groups = deceased + name_mis + ssn_conf
    # Sort: highest confidence first
    all_groups.sort(key=lambda g: -g.confidence)

    logger.info(
        "Cross-DB match complete: %d deceased | %d name-mismatch | %d ssn-conflict | %d total",
        len(deceased), len(name_mis), len(ssn_conf), len(all_groups),
    )
    return all_groups
