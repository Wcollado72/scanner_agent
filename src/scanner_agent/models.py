from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── File scanning models ───────────────────────────────────────────────────

@dataclass(slots=True)
class FileRecord:
    path: Path
    source: str
    size_bytes: int
    modified_time: float
    sha256: Optional[str] = None
    extension: str = ""
    code_fingerprint: Optional[str] = None


@dataclass(slots=True)
class DuplicateGroup:
    strategy: str
    confidence: float
    records: list[FileRecord] = field(default_factory=list)


# ── Database / record scanning models ─────────────────────────────────────

@dataclass(slots=True)
class RowRecord:
    """Represents a single row fetched from a database table."""

    # Identity
    row_id: str                     # Primary key or unique identifier from the source table
    source_table: str               # e.g. "electores", "licencias"
    source_db: str                  # Connection hint: db path, name, or alias

    # Canonical match fields (populated by the scanner based on field_config)
    nombre: Optional[str] = None
    apellido_paterno: Optional[str] = None
    apellido_materno: Optional[str] = None
    fecha_nacimiento: Optional[str] = None   # ISO-8601 string: YYYY-MM-DD
    edad: Optional[int] = None
    genero: Optional[str] = None
    direccion: Optional[str] = None
    municipio: Optional[str] = None
    seguro_social: Optional[str] = None      # Masked/partial: ***-**-XXXX

    # Contact
    telefono: Optional[str] = None           # e.g. "787-555-1234"
    tipo_telefono: Optional[str] = None      # "Movil" | "Residencia" | "Trabajo"

    # Computed
    nombre_normalizado: Optional[str] = None  # Uppercase, stripped, no accents
    row_hash: Optional[str] = None            # SHA-256 of canonical fields

    # Raw row for reporting / audit trail
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def nombre_completo(self) -> str:
        parts = filter(None, [self.nombre, self.apellido_paterno, self.apellido_materno])
        return " ".join(parts)

    @property
    def is_possibly_deceased(self) -> bool:
        """Flags records where edad > 100 or DOB would imply extreme age."""
        if self.edad is not None and self.edad > 100:
            return True
        return False

    @property
    def has_anomalous_age(self) -> bool:
        """Flags records with DOB in the future or age > 115 (implausible)."""
        if self.edad is not None and (self.edad < 0 or self.edad > 115):
            return True
        return False


@dataclass(slots=True)
class RecordMatchGroup:
    """A group of RowRecords that the matching engine believes may refer to the same person."""

    strategy: str           # e.g. "exact_hash", "name_dob_fuzzy", "ssn_cross", "deceased_flag"
    confidence: float       # 0.0 - 1.0
    flag: str               # "EXACT_DUPLICATE" | "NEAR_DUPLICATE" | "POSSIBLE_DECEASED" | "ANOMALY"
    records: list[RowRecord] = field(default_factory=list)
    notes: str = ""         # Human-readable explanation for the reviewer
