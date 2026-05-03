# SECURITY.md

## Security Principles

`scanner_agent` is designed with a safe-first philosophy.

## Current Security Rules

- No deletion in Phase 1
- No automatic movement of files
- No automatic rename actions
- Sensitive credentials must never be committed
- Real API keys must be stored only in local environment files
- System-sensitive directories must be excluded from scanning by default

## Secrets Handling

Never commit:

- `.env`
- `credentials.json`
- `token.json`

Use `.env.example` for documentation only.

## Responsible Development

Before publishing to GitHub:

- rotate any exposed secrets
- review commit history for leaked credentials
- verify `.gitignore`
- test in a sandbox folder before scanning real user directories

## Future Security Work

Planned improvements:

- confidence thresholds
- quarantine workflow instead of delete
- tamper-resistant logs
- connector-specific permission minimization
- secure GUI permission prompts