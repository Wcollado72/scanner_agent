from pathlib import Path

from scanner_agent.detection.duplicate_engine import group_exact_duplicates
from scanner_agent.models import FileRecord


def test_group_exact_duplicates_detects_duplicates() -> None:
    a = FileRecord(Path("a.txt"), "local", 10, 1.0, sha256="abc")
    b = FileRecord(Path("b.txt"), "local", 10, 2.0, sha256="abc")
    c = FileRecord(Path("c.txt"), "local", 12, 3.0, sha256="xyz")

    groups = group_exact_duplicates([a, b, c])

    assert len(groups) == 1
    assert len(groups[0].records) == 2