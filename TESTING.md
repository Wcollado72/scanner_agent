# TESTING.md

## Purpose

This document explains how to test `scanner_agent` safely during Phase 1.

## Safety Reminder

Phase 1 must never delete, move, quarantine, or rename user files.

## Test Types

### 1. Unit Tests

Run:

```bash
pytest
```

Expected result:

- all tests pass
- no real user files are modified

### 2. Manual Local Scan Test

Create a temporary folder with:

- one unique file
- two exact duplicate files
- one system-like file that should be excluded manually if placed in excluded paths

Example:

```text
sandbox_test/
├─ unique.txt
├─ duplicate_a.txt
├─ duplicate_b.txt
```

Make `duplicate_a.txt` and `duplicate_b.txt` contain the same text.

Run:

```bash
python -m scanner_agent --path "C:\path\to\sandbox_test"
```

Expected result:

- inventory report includes scanned files
- duplicate report includes one exact duplicate group
- no files are deleted or changed

### 3. Exclusion Validation

Create or simulate paths that include:

- AppData
- node_modules
- .venv
- __pycache__

Expected result:

- excluded paths are not scanned

### 4. Logging Validation

Verify that:

- the app logs startup
- the app logs report generation
- warnings appear for inaccessible files
- no delete actions appear in logs

## Future Testing Roadmap

Future phases should add:

- cloud connector tests
- email connector tests
- code similarity tests
- confidence scoring tests
- quarantine workflow tests
- GUI integration tests