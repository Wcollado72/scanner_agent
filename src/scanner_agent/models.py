from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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
    