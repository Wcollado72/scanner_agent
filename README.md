# scanner_agent

`scanner_agent` is a safe-first file intelligence and duplicate detection engine designed to evolve into a future cross-platform application.

## Current Phase

Phase 1 focuses on:

- local file inventory
- exact duplicate detection using SHA-256 plus file size
- strict safety exclusions
- JSON reporting
- future-ready architecture for app integration

## Safety Principles

- No deletion in Phase 1
- No quarantine in Phase 1
- No automatic file modification
- System-sensitive paths are excluded
- Project metadata and risky file types are excluded by default

## Why This Project Exists

This project is designed to help users identify duplicate and overlapping files across local storage, external drives, cloud sources, and future integrations such as email attachments.

The long-term product vision is an AI-assisted file intelligence application that can:

- distinguish exact duplicates from likely versions
- analyze code-aware similarity
- identify the most complete and most recent versions
- support future GUI workflows and safe user decisions

## Planned Future Capabilities

- external drive event detection
- cloud inventory connectors
- email attachment inventory
- code-aware similarity scoring
- confidence-based recommendations
- quarantine workflows
- future Tauri desktop app integration
- future mobile-connected experiences

## Installation

Create a virtual environment:

```bash
python -m venv .venv
```

### Windows

Activate the virtual environment:

```bash
.venv\Scripts\activate
```

### Install dependencies

```bash
pip install -e .[dev]
```

## Environment Setup

Create a local `.env` file based on `.env.example`.

Example:

```env
GEMINI_API_KEY=replace_with_real_value
GOOGLE_CLIENT_SECRETS_PATH=credentials.json
GOOGLE_TOKEN_PATH=token.json
DEFAULT_SCAN_PATH=Documents
LOG_LEVEL=INFO
```

Do not commit real secrets to GitHub.

## Run

```bash
python -m scanner_agent --path "C:\Users\YourUser\Documents"
```

Or, after editable install:

```bash
scanner-agent --path "C:\Users\YourUser\Documents"
```

## Output

Reports are written to the `reports/` directory:

- `inventory.json`
- `duplicates.json`

## Development Notes

This repository uses a `src/` layout and a modular architecture so the scanning engine can later power a desktop application without a full rewrite.