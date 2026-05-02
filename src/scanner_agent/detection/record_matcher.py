"""
record_matcher.py  v0.5.0 — Fuzzy matching engine for database records.

Detects:
  1. Exact duplicates     — same canonical hash
  2. Near-duplicates      — weighted fuzzy name + DOB + SSN + municipio scoring
  3. SSN identity conflict— same SSN on names with low similarity (fraud/critical error)
  4. Address clusters     — anomalous concentration of voters at same address
  5. Possible deceased    — edad > 100
  6. Anomalous records    — edad < 0 or edad > 115

Confidence is now weighted instead of flat:
  name_similarity: 40-65 pts  (scales with similarity above threshold)
  same DOB:        +25 pts
  same SSN:        +20 pts
  same municipio:  + 5 pts
  Max capped at 0.99 (1.00 reserved for exact hash duplicates)

Uses only Python standard library (difflib). No external dependencies.
"""

from __future__ import annotations

import difflib
import logging
from collections import defaultdict

from scanner_agent.models import RecordMatchGroup, RowRecord

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────
EXACT_HASH_CONFIDENCE    = 1.00
NAME_DOB_FUZZY_THRESHOLD = 0.82   # Minimum name similarity to enter near-dup comparison
SSN_CONFLICT_NAME_MAX    = 0.50   # If name_sim < this AND same SSN → identity conflict
SSN_CONFLICT_CONFIDENCE  = 0.95   # Same SSN, clearly different name
DECEASED_AGE_THRESHOLD   = 100
ANOMALY_AGE_MAX          = 115

# Confidence weights for near-duplicates
W_NAME_BASE   = 0.40  # Confidence at exactly the name threshold
W_NAME_RANGE  = 0.25  # Additional confidence from 1.0 name similarity
W_SAME_DOB    = 0.25  # Bonus for matching date of birth
W_SAME_SSN    = 0.20  # Bonus for matching SSN
W_SAME_MUN    = 0.05  # Bonus for same municipio
CONF_MAX      = 0.99  # Hard cap (1.00 reserved for exact hash)


# ── Internal helpers ───────────────────────────────────────────────────────

def _name_sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _same_dob(a: RowRecord, b: RowRecord) -> bool:
    return bool(a.fecha_nacimiento and b.fecha_nacimiento
                and a.fecha_nacimiento == b.fecha_nacimiento)


def _same_ssn(a: RowRecord, b: RowRecord) -> bool:
    return bool(a.seguro_social and b.seguro_social
                and a.seguro_social == b.seguro_social
                and not a.seguro_social.startswith("***-**-0000"))


def _same_mun(a: RowRecord, b: RowRecord) -> bool:
    return bool(a.municipio and b.municipio
                and a.municipio.upper() == b.municipio.upper())


def _weighted_confidence(name_sim: float, dob: bool, ssn: bool, mun: bool,
                         threshold: float = NAME_DOB_FUZZY_THRESHOLD) -> float:
    """
    Compute weighted confidence for a near-duplicate pair.

    name_sim in [threshold, 1.0] maps linearly from W_NAME_BASE to
    W_NAME_BASE + W_NAME_RANGE. Additional bonuses for DOB, SSN, municipio.
    """
    name_range = 1.0 - threshold
    w_name = W_NAME_BASE + (name_sim - threshold) / name_range * W_NAME_RANGE
    score  = w_name
    score += W_SAME_DOB if dob else 0.0
    score += W_SAME_SSN if ssn else 0.0
    score += W_SAME_MUN if mun else 0.0
    return min(CONF_MAX, round(score, 4))


def _describe_signals(name_sim: float, dob: bool, ssn: bool, mun: bool,
                      a: RowRecord, b: RowRecord) -> str:
    signals = [f"similitud de nombre {name_sim:.0%}"]
    if dob:
        signals.append("misma fecha de nacimiento")
    if ssn:
        signals.append("mismo SSN")
    if mun:
        signals.append("mismo municipio")
    return (
        f"{a.nombre_normalizado!r} vs {b.nombre_normalizado!r} — "
        + ", ".join(signals) + ". Posible variante de grafia o error de captura."
    )


# ── Pass 1: Exact duplicates ───────────────────────────────────────────────

def find_exact_duplicates(records: list[RowRecord]) -> list[RecordMatchGroup]:
    """Group records that share the same canonical hash."""
    buckets: dict[str, list[RowRecord]] = defaultdict(list)
    for r in records:
        if r.row_hash:
            buckets[r.row_hash].append(r)

    groups: list[RecordMatchGroup] = []
    for group in buckets.values():
        if len(group) > 1:
            groups.append(RecordMatchGroup(
                strategy="exact_hash",
                confidence=EXACT_HASH_CONFIDENCE,
                flag="EXACT_DUPLICATE",
                records=group,
                notes=(
                    f"{len(group)} registros con nombre, apellidos, DOB y SSN identicos. "
                    "Duplicado exacto — requiere revision y consolidacion."
                ),
            ))

    logger.info("Exact duplicates: %d groups", len(groups))
    return groups


# ── Pass 2: Near-duplicates (fuzzy name within blocks) ────────────────────

def find_near_duplicates(
    records: list[RowRecord],
    name_threshold: float = NAME_DOB_FUZZY_THRESHOLD,
) -> list[RecordMatchGroup]:
    """
    Detect near-duplicate pairs using fuzzy name matching with weighted confidence.

    Blocking strategy: group by (municipio, birth_year) to avoid O(n²).
    Within each block, compare all pairs above the name threshold and compute
    a weighted confidence score from name similarity + DOB + SSN + municipio.
    """
    blocks: dict[tuple, list[RowRecord]] = defaultdict(list)
    for r in records:
        mun  = (r.municipio or "UNKNOWN").upper()
        year = r.fecha_nacimiento[:4] if r.fecha_nacimiento and len(r.fecha_nacimiento) >= 4 else "0000"
        blocks[(mun, year)].append(r)

    already_paired: set[frozenset[str]] = set()
    groups: list[RecordMatchGroup] = []

    for block in blocks.values():
        if len(block) < 2:
            continue
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b = block[i], block[j]
                if a.row_hash and b.row_hash and a.row_hash == b.row_hash:
                    continue  # already exact duplicate

                sim = _name_sim(a.nombre_normalizado, b.nombre_normalizado)
                if sim < name_threshold:
                    continue

                key = frozenset([a.row_id, b.row_id])
                if key in already_paired:
                    continue
                already_paired.add(key)

                dob = _same_dob(a, b)
                ssn = _same_ssn(a, b)
                mun = _same_mun(a, b)
                conf = _weighted_confidence(sim, dob, ssn, mun, name_threshold)

                groups.append(RecordMatchGroup(
                    strategy="name_dob_fuzzy",
                    confidence=conf,
                    flag="NEAR_DUPLICATE",
                    records=[a, b],
                    notes=_describe_signals(sim, dob, ssn, mun, a, b),
                ))

    logger.info("Near-duplicates (fuzzy name): %d pairs", len(groups))
    return groups


# ── Pass 3: SSN identity conflicts ────────────────────────────────────────

def find_ssn_conflicts(records: list[RowRecord]) -> list[RecordMatchGroup]:
    """
    Detect records that share the same SSN but have clearly different names
    (name similarity below SSN_CONFLICT_NAME_MAX).

    This is a stronger signal than a near-duplicate: it suggests either a
    critical data entry error or potential identity fraud. These appear in the
    review queue as NEAR_DUPLICATE with strategy 'ssn_identity_conflict' and
    high confidence (0.95).
    """
    ssn_index: dict[str, list[RowRecord]] = defaultdict(list)
    for r in records:
        if r.seguro_social and not r.seguro_social.startswith("***-**-0000"):
            ssn_index[r.seguro_social].append(r)

    already_paired: set[frozenset[str]] = set()
    groups: list[RecordMatchGroup] = []

    for ssn, bucket in ssn_index.items():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                if a.row_hash and b.row_hash and a.row_hash == b.row_hash:
                    continue

                key = frozenset([a.row_id, b.row_id])
                if key in already_paired:
                    continue

                sim = _name_sim(a.nombre_normalizado, b.nombre_normalizado)
                if sim >= NAME_DOB_FUZZY_THRESHOLD:
                    # High name similarity → already caught by find_near_duplicates
                    continue

                already_paired.add(key)
                groups.append(RecordMatchGroup(
                    strategy="ssn_identity_conflict",
                    confidence=SSN_CONFLICT_CONFIDENCE,
                    flag="NEAR_DUPLICATE",
                    records=[a, b],
                    notes=(
                        f"Mismo SSN ({ssn}) compartido entre personas con nombres distintos: "
                        f"{a.nombre_completo!r} vs {b.nombre_completo!r} "
                        f"(similitud {sim:.0%}). "
                        "Posible error de captura critico o conflicto de identidad."
                    ),
                ))

    logger.info("SSN identity conflicts: %d pairs", len(groups))
    return groups


# ── Pass 4: Deceased / anomaly flags ──────────────────────────────────────

def find_deceased_and_anomalies(records: list[RowRecord]) -> list[RecordMatchGroup]:
    """Flag individual records with extreme or impossible age values."""
    groups: list[RecordMatchGroup] = []
    for r in records:
        if r.has_anomalous_age:
            groups.append(RecordMatchGroup(
                strategy="age_anomaly",
                confidence=0.99,
                flag="ANOMALY",
                records=[r],
                notes=(
                    f"Edad anomala: {r.edad} anos (DOB: {r.fecha_nacimiento}). "
                    "Posible error de captura de fecha de nacimiento o DOB en el futuro."
                ),
            ))
        elif r.is_possibly_deceased:
            groups.append(RecordMatchGroup(
                strategy="deceased_flag",
                confidence=0.80,
                flag="POSSIBLE_DECEASED",
                records=[r],
                notes=(
                    f"Edad: {r.edad} anos (DOB: {r.fecha_nacimiento}). "
                    "Posible persona fallecida — validar contra Registro Demografico."
                ),
            ))

    logger.info("Deceased/anomaly flags: %d records", len(groups))
    return groups


# ── Full pipeline ──────────────────────────────────────────────────────────

def run_full_match(records: list[RowRecord]) -> list[RecordMatchGroup]:
    """
    Run all matching passes and return a unified list of RecordMatchGroup.

    Order:
      1. Exact duplicates      (deterministic, highest confidence)
      2. Near-duplicates       (fuzzy name within blocks, weighted confidence)
      3. SSN identity conflicts (cross-block SSN, high confidence)
      4. Address clusters       (anomalous voter concentration per address)
      5. Deceased / anomaly    (single-record flags)
    """
    from scanner_agent.detection.address_analyzer import find_address_clusters

    exact = find_exact_duplicates(records)

    # Exclude exact-dup records from near-dup and SSN-conflict scans
    exact_ids: set[str] = {r.row_id for g in exact for r in g.records}
    remaining = [r for r in records if r.row_id not in exact_ids]

    near     = find_near_duplicates(remaining)
    ssn_cf   = find_ssn_conflicts(remaining)
    addr_cl  = find_address_clusters(records)   # uses ALL records (clusters span exact dups)
    flags    = find_deceased_and_anomalies(records)

    all_groups = exact + near + ssn_cf + addr_cl + flags

    logger.info(
        "Full match: %d exact | %d near-dup | %d ssn-conflict | %d addr-clusters | %d flags | %d total",
        len(exact), len(near), len(ssn_cf), len(addr_cl), len(flags), len(all_groups),
    )
    return all_groups
