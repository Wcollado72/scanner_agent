# scanner_agent

Scanner Agent is a Python command-line tool that scans directories for duplicate files using robust hashing and exclusion rules. It generates JSON reports you can integrate into other tools or review manually.

> Status: early but stable — core duplicate detection and exclusions are implemented and fully covered by tests.

---

## Table of Contents

- [Features](#features)
- [Project Goals](#project-goals)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Running Tests](#running-tests)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

- **Exact duplicate detection** based on SHA-256 hashes for reliable comparison.
- **Configurable exclusions** to skip system paths, project metadata and other noise.
- **Structured JSON reports**:
  - `duplicates.json` – groups of duplicate files by content hash.
  - `inventory.json` – inventory of scanned files and basic metadata.
- **Modular design** ready for future scanners:
  - local filesystem, cloud, e-mail and external scanners are scaffolded.
- **Safety and policy hooks** for future guardrails around what gets scanned or reported.

---

## Project Goals

This project is designed as a **teaching and real-world** tool:

- Provide a clean, test-driven example of a Python CLI scanner.
- Be a foundation for more advanced security / compliance agents.
- Show good practices with:
  - `pyproject.toml`
  - `pytest`-based tests
  - sensible `.gitignore`
  - clear directory layout and reporting.

---

## Installation

### Prerequisites

- Python **3.11+** (the project is developed on a modern Python 3 version).
- Git (if you are cloning from GitHub).
- A terminal (PowerShell, cmd, or bash).

### Clone the repository

```bash
git clone https://github.com/Wcollado72/scanner_agent.git
cd scanner_agent
```

### Create and activate a virtual environment

```bash
python -m venv .venv
# On Windows (PowerShell)
.venv\Scripts\Activate.ps1
# On macOS/Linux
source .venv/bin/activate
```

### Install dependencies

This project uses `pyproject.toml` with a standard dependency section. From the project root:

```bash
pip install -e .
```

The `-e` (editable) install lets you run the CLI as a package during development.

---

## Quick Start

The CLI entry point lives under the `scanner_agent` package. Depending on how the project is wired in `pyproject.toml`, you’ll typically run it using:

```bash
python -m scanner_agent --help
```

Or, if an entry point script is configured, something like:

```bash
scanner-agent --help
```

> Note: In this early stage, the CLI focuses on scanning a directory, applying exclusions, and writing JSON reports under the `reports/` folder.

A typical scan would look like:

```bash
# From the project root
python -m scanner_agent \
  --root-path "C:\path\to\scan" \
  --output-dir ".\reports"
```

Check the `reports/` directory afterwards:

- `duplicates.json` – exact duplicate groups.
- `inventory.json` – all scanned files with hash and basic info.

---

## How It Works

At a high level:

1. **Hash Engine** – `src/scanner_agent/detection/hash_engine.py`
   - Computes SHA-256 hashes for files.
   - Encapsulates reading and hashing logic.

2. **Duplicate Engine** – `src/scanner_agent/detection/duplicate_engine.py`
   - Groups files by content hash.
   - Returns only groups that contain true duplicates.

3. **Exclusions / Safety** – `src/scanner_agent/safety/exclusions.py`
   - Central place to define which paths or patterns should be skipped.
   - Tests ensure important system paths and project files are not scanned.

4. **Reporting** – `src/scanner_agent/reporting/report_writer.py`
   - Writes JSON reports (`duplicates.json`, `inventory.json`).
   - Designed so future formats (CSV, HTML, etc.) can be added cleanly.

5. **CLI** – `src/scanner_agent/cli.py` and `__main__.py`
   - Parses command-line arguments.
   - Wires together scanning, exclusion rules and reporting.

The current code is intentionally modular so it can grow into a richer “agent” with additional scanners (cloud repositories, e-mail, external APIs) without rewriting the core. 

---

## Project Structure

High-level layout of the repository:

```text
scanner_agent/
├── README.md
├── pyproject.toml
├── SECURITY.md
├── TESTING.md
├── .env.example
├── .gitignore
├── src/
│   └── scanner_agent/
│       ├── __init__.py
│       ├── __main__.py          # Package entry point
│       ├── cli.py               # CLI argument parsing / wiring
│       ├── config.py            # Configuration helpers
│       ├── logging_config.py    # Logging setup
│       ├── models.py            # Core data models
│       ├── detection/
│       │   ├── __init__.py
│       │   ├── hash_engine.py   # SHA-256 hashing
│       │   ├── duplicate_engine.py
│       │   └── code_fingerprint.py
│       ├── safety/
│       │   ├── __init__.py
│       │   ├── exclusions.py    # Exclusion rules
│       │   └── decision_guard.py
│       ├── reporting/
│       │   ├── __init__.py
│       │   └── report_writer.py # JSON reports
│       ├── scanners/
│       │   ├── __init__.py
│       │   ├── local_scanner.py
│       │   ├── external_scanner.py
│       │   ├── cloud_scanner.py
│       │   └── email_scanner.py
│       └── future/
│           ├── __init__.py
│           └── app_hooks.py     # Hooks for future app integrations
└── tests/
    ├── test_duplicate_engine.py
    ├── test_exclusions.py
    └── test_hash_engine.py
```

---

## Running Tests

The project uses `pytest` for automated tests.

From the project root (with the virtual environment activated):

```bash
pytest
```

For more verbose output:

```bash
pytest -v
```

Tests currently cover:

- SHA-256 hashing behavior.
- Duplicate grouping logic.
- Exclusion rules for system paths and project files.

---

## Roadmap

Planned and potential future work:

- **CLI UX improvements**
  - Better progress output and summary stats.
  - Clearer exit codes and error messages.

- **More scanners**
  - Cloud repository scanning (e.g., GitHub paths).
  - E-mail attachments scanning.
  - External API-based scanners.

- **Richer reporting**
  - Additional formats (CSV, HTML) and human-friendly summaries.
  - Option to limit report size or filter by extension / size.

- **Safety & policy**
  - More configurable guardrails around what is scanned and stored in reports.
  - Safer defaults for sensitive paths.

---

## License

This project is intended as an educational and utility tool.  
Add a license file (for example, MIT) to clarify usage rights before using it in production or publishing derived work.