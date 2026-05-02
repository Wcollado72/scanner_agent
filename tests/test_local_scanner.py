"""Tests for local_scanner: real filesystem behavior, exclusions, and edge cases."""

import logging
from pathlib import Path

import pytest

from scanner_agent.scanners.local_scanner import scan_local_paths


def test_scan_nonexistent_path_returns_empty_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-existent path should log a warning and return an empty record list."""
    missing = tmp_path / "does_not_exist"

    with caplog.at_level(logging.WARNING, logger="scanner_agent.scanners.local_scanner"):
        records = scan_local_paths([missing])

    assert records == [], "Expected no records for a missing path"
    assert any("does not exist" in msg for msg in caplog.messages), (
        "Expected a warning mentioning the missing path"
    )


def test_scan_produces_one_record_per_file(tmp_path: Path) -> None:
    """Every regular file in the target folder should produce exactly one record."""
    (tmp_path / "alpha.txt").write_text("aaa", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("bbb", encoding="utf-8")
    (tmp_path / "gamma.txt").write_text("ccc", encoding="utf-8")

    records = scan_local_paths([tmp_path])

    assert len(records) == 3


def test_scan_excludes_system_directories(tmp_path: Path) -> None:
    """Files inside excluded system directories must not appear in records."""
    app_data = tmp_path / "AppData" / "Local"
    app_data.mkdir(parents=True)
    (app_data / "secret.txt").write_text("sensitive", encoding="utf-8")

    venv_dir = tmp_path / ".venv" / "Lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "site.py").write_text("venv file", encoding="utf-8")

    node_mod = tmp_path / "node_modules" / "pkg"
    node_mod.mkdir(parents=True)
    (node_mod / "index.js").write_text("module", encoding="utf-8")

    # One safe file that should be included
    (tmp_path / "safe.txt").write_text("keep me", encoding="utf-8")

    records = scan_local_paths([tmp_path])
    record_paths = [str(r.path) for r in records]

    assert len(records) == 1, f"Expected only safe.txt, got: {record_paths}"
    assert any("safe.txt" in p for p in record_paths)


def test_scan_record_fields_are_populated(tmp_path: Path) -> None:
    """Each FileRecord must have the expected fields populated after scanning."""
    sample = tmp_path / "notes.txt"
    sample.write_text("hello scanner", encoding="utf-8")

    records = scan_local_paths([tmp_path])

    assert len(records) == 1
    record = records[0]
    assert record.path == sample
    assert record.source == "local"
    assert record.size_bytes > 0
    assert record.modified_time > 0
    assert record.extension == ".txt"
    # sha256 is not computed by the scanner itself — it's done by the CLI hashing pass
    assert record.sha256 is None
