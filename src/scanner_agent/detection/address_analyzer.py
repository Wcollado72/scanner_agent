"""
scanner_agent.detection.address_analyzer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Puerto Rico address normalization and cluster detection.

Normalizes addresses to a canonical form for comparison, then groups records
by (normalized_address, municipio) and flags concentrations that exceed the
expected maximum for each address type.

Address types and default thresholds (soft / hard):
  RESIDENTIAL   Calle, Avenida, Paseo + house number          8  / 20
  URBANIZATION  Urb. prefix + calle + number                 10  / 25
  APARTMENT     Condo, Edificio, Torre + unit                 40  / 100
  RURAL         HC-XX Box N  or  RR-X Box N                   4  / 10
  PO_BOX        PO Box, Apartado Postal                        0  / 0  (never valid)
  COMMERCIAL    Suite, Local, Oficina, Plaza                   2  / 5
  UNKNOWN       No pattern matched                             8  / 20

Confidence:
  count >= hard_threshold   -> 0.97  (almost certainly fraudulent or data error)
  count >= soft_threshold   -> 0.75  (suspicious, needs review)
  PO_BOX / COMMERCIAL any   -> 0.90  (invalid or suspicious address type)
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict

from scanner_agent.models import RecordMatchGroup, RowRecord

logger = logging.getLogger(__name__)

# ── Abbreviation maps ──────────────────────────────────────────────────────

_ABBR: list[tuple[str, str]] = [
    # Street prefixes
    (r"\bC/\b",             "CALLE"),
    (r"\bCLL\b",            "CALLE"),
    (r"\bAVDA\b",           "AVE"),
    (r"\bAV\b",             "AVE"),
    (r"\bAVENIDA\b",        "AVE"),
    (r"\bPASAJE\b",         "PAS"),
    (r"\bCARRETERA\b",      "CARR"),
    (r"\bCARR\.\b",         "CARR"),
    # Unit types
    (r"\bAPARTAMENTO\b",    "APT"),
    (r"\bAPARTAMENT\b",     "APT"),
    (r"\bAPTO\b",           "APT"),
    (r"\bUNIDAD\b",         "APT"),
    (r"\bUNIT\b",           "APT"),
    (r"\bSTE\b",            "SUITE"),
    (r"\bOFICINA\b",        "OFIC"),
    (r"\bOFIC\.\b",         "OFIC"),
    # Place classifiers
    (r"\bURBANIZACION\b",   "URB"),
    (r"\bURBANIZACIÓN\b",   "URB"),
    (r"\bURBAN\b",          "URB"),
    (r"\bBARRIO\b",         "BO"),
    (r"\bCONDOMINIO\b",     "COND"),
    (r"\bCONDOMINIUM\b",    "COND"),
    (r"\bCONDO\b",          "COND"),
    (r"\bEDIFICIO\b",       "EDIF"),
    (r"\bRESIDENCIAL\b",    "RES"),
    (r"\bRESID\b",          "RES"),
    (r"\bJARDINES\b",       "JARD"),
    (r"\bQUINTAS\b",        "QNTS"),
    (r"\bVILLAS\b",         "VIL"),
    (r"\bSECTOR\b",         "SEC"),
    # Noise words
    (r"\bNUM\.\b",          ""),
    (r"\bNUM\b",            ""),
    (r"\bNO\.\b",           ""),
]

_ABBR_COMPILED = [(re.compile(pat), rep) for pat, rep in _ABBR]

# ── Type classification patterns ───────────────────────────────────────────

_IS_PO_BOX     = re.compile(r"\b(PO\s*BOX|P\.?O\.?\s*BOX|APARTADO\s*POSTAL|APARTADO|BUZON\s*POSTAL)\b")
_IS_RURAL      = re.compile(r"\b(HC[-\s]?\d{1,3}|RR[-\s]?\d{1,2})(\s*(BOX|BX)\s*\d)?")
_IS_COMMERCIAL = re.compile(r"\b(SUITE|LOCAL|OFIC|OFICINA|PLAZA\s+\w|CENTRO\s+COM|MALL|CORP\.?\s+CTR)\b")
_IS_APARTMENT  = re.compile(r"\b(COND|CONDOMINIO|EDIF|EDIFICIO|TORRE\s+\w|APT\s+[A-Z0-9]+|APTO\s+[A-Z0-9]+)\b")
_IS_URB        = re.compile(r"\bURB\b")

# ── Thresholds ─────────────────────────────────────────────────────────────

THRESHOLDS: dict[str, dict[str, int]] = {
    "RESIDENTIAL":   {"soft":  8, "hard":  20},
    "URBANIZATION":  {"soft": 10, "hard":  25},
    "APARTMENT":     {"soft": 40, "hard": 100},
    "RURAL":         {"soft":  4, "hard":  10},
    "PO_BOX":        {"soft":  0, "hard":   0},
    "COMMERCIAL":    {"soft":  2, "hard":   5},
    "UNKNOWN":       {"soft":  8, "hard":  20},
}

CONF_HARD          = 0.97   # count >= hard threshold
CONF_SOFT          = 0.75   # soft <= count < hard
CONF_INVALID_TYPE  = 0.90   # PO Box / commercial regardless of count


# ── Normalization ──────────────────────────────────────────────────────────

def normalize_pr_address(address: str | None) -> str:
    """
    Return a canonical uppercase ASCII version of a PR address for comparison.

    Steps:
      1. NFKD decomposition + strip combining chars (removes accents)
      2. Uppercase
      3. Replace # with space (house number separator)
      4. Remove punctuation: periods, commas, semicolons, quotes
      5. Apply abbreviation normalization table
      6. Collapse multiple spaces
    """
    if not address:
        return ""

    # 1. Strip accents
    nfkd = unicodedata.normalize("NFKD", address)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))

    # 2. Uppercase
    s = s.upper()

    # 3-4. Punctuation
    s = s.replace("#", " ")
    s = re.sub(r"[.,;:'\"]", "", s)

    # 5a. Handle C/ prefix before general loop (/ breaks \b word boundaries)
    s = re.sub(r'\bC/', 'CALLE ', s)

    # 5b. Abbreviations
    for pattern, replacement in _ABBR_COMPILED:
        s = pattern.sub(replacement, s)

    # 6. Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    return s


def classify_address_type(normalized: str) -> str:
    """
    Classify a normalized PR address into one of the recognized types.
    Returns the type string used as key in THRESHOLDS.
    """
    if _IS_PO_BOX.search(normalized):
        return "PO_BOX"
    if _IS_RURAL.search(normalized):
        return "RURAL"
    if _IS_COMMERCIAL.search(normalized):
        return "COMMERCIAL"
    if _IS_APARTMENT.search(normalized):
        return "APARTMENT"
    if _IS_URB.search(normalized):
        return "URBANIZATION"
    if normalized:
        return "RESIDENTIAL"
    return "UNKNOWN"


# ── Cluster detection ──────────────────────────────────────────────────────

def find_address_clusters(
    records: list[RowRecord],
    thresholds: dict[str, dict[str, int]] | None = None,
) -> list[RecordMatchGroup]:
    """
    Detect addresses with an abnormal number of voter registrations.

    Groups records by (normalized_address, municipio). Records without an
    address are skipped. Flags groups that exceed the soft threshold for their
    address type, with higher confidence for groups above the hard threshold.

    PO Box and commercial addresses are always flagged (invalid voter addresses
    in PR electoral law) regardless of count.

    Returns one RecordMatchGroup per flagged address, containing all records
    at that address so reviewers can see the full picture.
    """
    t = thresholds or THRESHOLDS

    # Build address index
    addr_index: dict[tuple[str, str], list[RowRecord]] = defaultdict(list)
    for r in records:
        if not r.direccion:
            continue
        norm = normalize_pr_address(r.direccion)
        if not norm:
            continue
        mun = (r.municipio or "DESCONOCIDO").upper().strip()
        addr_index[(norm, mun)].append(r)

    groups: list[RecordMatchGroup] = []

    for (addr, mun), addr_records in addr_index.items():
        addr_type  = classify_address_type(addr)
        thres      = t.get(addr_type, t["UNKNOWN"])
        count      = len(addr_records)
        soft, hard = thres["soft"], thres["hard"]

        # PO Box / commercial: always flag even with 1 record
        if addr_type in ("PO_BOX", "COMMERCIAL") and count >= 1:
            expected_note = "Tipo de direccion invalido para registro electoral"
            conf = CONF_INVALID_TYPE
        elif count > hard and hard > 0:
            expected_note = f"Maximo esperado para {addr_type}: {hard} registros"
            conf = CONF_HARD
        elif count > soft and soft > 0:
            expected_note = f"Umbral de alerta para {addr_type}: {soft} registros"
            conf = CONF_SOFT
        else:
            continue  # Normal concentration

        severity = (
            "CRITICO" if conf >= CONF_HARD or addr_type in ("PO_BOX",)
            else "SOSPECHOSO" if conf >= CONF_SOFT
            else "ALERTA"
        )

        groups.append(RecordMatchGroup(
            strategy="address_concentration",
            confidence=conf,
            flag="ADDRESS_CLUSTER",
            records=addr_records,
            notes=(
                f"Direccion: '{addr}' — {mun} — "
                f"{count} registros activos. "
                f"Tipo: {addr_type}. {expected_note}. "
                f"Severidad: {severity}."
            ),
        ))

    groups.sort(key=lambda g: (-g.confidence, -len(g.records)))
    logger.info("Address clusters found: %d flagged addresses", len(groups))
    return groups
