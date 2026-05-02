# TESTING.md

## Purpose

This document explains how to test `scanner_agent` safely during Phase 1.

## Safety Reminder

Phase 1 must never delete, move, quarantine, or rename user files.
All scans are read-only. All tests use temporary directories managed by pytest.

---

## Running the Test Suite

From the project root with `.venv` activated:

```bash
pytest
```

For verbose output:

```bash
pytest -v
```

Expected result: all tests pass, no real user files are touched.

---

## Test Coverage (as of 2026-05-02)

| File                       | What it covers                                      |
|----------------------------|-----------------------------------------------------|
| `test_hash_engine.py`      | SHA-256 hash correctness, return type, length       |
| `test_duplicate_engine.py` | Exact duplicate grouping by hash + size             |
| `test_exclusions.py`       | System path exclusion, project file exclusion       |
| `test_local_scanner.py`    | Scanner edge cases: missing paths, exclusions, fields |
| `test_report_writer.py`    | JSON output structure, metadata, empty reports      |

---

## Manual Integration Test

Create a sandbox folder with:

```text
sandboxtest_test/
├─ unique.txt          ← unique content
├─ duplicate_a.txt     ← identical content to duplicate_b.txt
├─ duplicate_b.txt     ← identical content to duplicate_a.txt
```

Run the scanner:

```bash
python -m scanner_agent --path ".\sandboxtest_test"
```

Expected results:

- `reports/inventory.json` → contains a `meta` section + `files` list with 3 entries
- `reports/duplicates.json` → contains a `meta` section + `duplicate_groups` with 1 group
- Terminal prints a clean summary with file count, duplicate groups, and elapsed time
- No files are deleted or modified

---

## Exclusion Validation

To manually verify exclusions, create subdirectories named:

- `AppData/`, `.venv/`, `node_modules/`, `__pycache__/`

Place files inside them and confirm they do **not** appear in `inventory.json`.

---

## Lint Check

```bash
ruff check .
```

All issues should be zero after cleanup.

---

## Future Testing Roadmap

- Database connector tests (`db_scanner.py`)
- Record normalization and entity matching tests
- Confidence scoring and decision guard tests
- Cloud and email scanner tests (Phase 3)
- End-to-end audit report tests
