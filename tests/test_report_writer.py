"""Tests for report_writer: JSON output structure and metadata inclusion."""

import json
from pathlib import Path

from scanner_agent.models import DuplicateGroup, FileRecord
from scanner_agent.reporting.report_writer import write_duplicates_report, write_inventory_report


def _make_record(name: str, sha: str = "abc123", size: int = 100) -> FileRecord:
    return FileRecord(
        path=Path(name),
        source="local",
        size_bytes=size,
        modified_time=1_700_000_000.0,
        sha256=sha,
        extension=Path(name).suffix,
    )


def test_inventory_report_has_expected_keys(tmp_path: Path) -> None:
    """inventory.json must contain top-level 'files' list with the right per-file keys."""
    records = [_make_record("a.txt"), _make_record("b.txt")]
    out = tmp_path / "inventory.json"

    write_inventory_report(out, records)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert "files" in data, "Top-level key 'files' is missing"
    assert len(data["files"]) == 2

    required_keys = {"path", "source", "size_bytes", "modified_time", "sha256", "extension"}
    for entry in data["files"]:
        assert required_keys.issubset(entry.keys()), f"Missing keys in file entry: {entry}"


def test_duplicates_report_has_expected_keys(tmp_path: Path) -> None:
    """duplicates.json must contain 'duplicate_groups' list with the right keys."""
    group = DuplicateGroup(
        strategy="exact_hash_match",
        confidence=1.0,
        records=[_make_record("x.txt", "aaa"), _make_record("y.txt", "aaa")],
    )
    out = tmp_path / "duplicates.json"

    write_duplicates_report(out, [group])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert "duplicate_groups" in data, "Top-level key 'duplicate_groups' is missing"
    assert len(data["duplicate_groups"]) == 1

    grp = data["duplicate_groups"][0]
    assert grp["strategy"] == "exact_hash_match"
    assert grp["confidence"] == 1.0
    assert len(grp["records"]) == 2

    record_keys = {"path", "source", "size_bytes", "sha256"}
    for r in grp["records"]:
        assert record_keys.issubset(r.keys()), f"Missing keys in record entry: {r}"


def test_reports_include_scan_metadata(tmp_path: Path) -> None:
    """When scan_meta is provided, both reports must include a 'meta' section."""
    meta = {
        "scan_timestamp": "2026-05-02T12:00:00+00:00",
        "scan_targets": ["C:\\scanner_agent\\sandboxtest_test"],
        "scanner": "local",
        "total_files_scanned": 3,
        "duplicate_groups_found": 1,
        "duration_seconds": 0.042,
    }

    inv_out = tmp_path / "inventory.json"
    dup_out = tmp_path / "duplicates.json"

    write_inventory_report(inv_out, [_make_record("f.txt")], scan_meta=meta)
    write_duplicates_report(dup_out, [], scan_meta=meta)

    inv_data = json.loads(inv_out.read_text(encoding="utf-8"))
    dup_data = json.loads(dup_out.read_text(encoding="utf-8"))

    assert "meta" in inv_data, "inventory.json missing 'meta' key"
    assert "meta" in dup_data, "duplicates.json missing 'meta' key"
    assert inv_data["meta"]["scanner"] == "local"
    assert dup_data["meta"]["total_files_scanned"] == 3


def test_empty_inventory_report_is_valid(tmp_path: Path) -> None:
    """Writing an inventory with zero records must produce valid JSON with an empty list."""
    out = tmp_path / "inventory.json"
    write_inventory_report(out, [])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["files"] == []


def test_empty_duplicates_report_is_valid(tmp_path: Path) -> None:
    """Writing a duplicates report with zero groups must produce valid JSON."""
    out = tmp_path / "duplicates.json"
    write_duplicates_report(out, [])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["duplicate_groups"] == []
