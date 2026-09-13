# Repository Guidelines

## Project Structure & Module Organization

The stack is running: `docker-compose.yml` (PostgreSQL, Redis, Oxidized, backend), a FastAPI service under `backend/app/`, its tests under `backend/tests/`, deployment helpers in `deploy/`, operational scripts in `scripts/`, and `docs/` with product context, architecture, agent boundaries, roadmap, security and per-phase notes. `.env.example` documents non-secret configuration defaults.

Phases 1 to 3 are implemented (inventory in PostgreSQL, `source: http` for Oxidized, Git backups with events and diffs) and Phase 4 is partially implemented (deterministic configuration auditing, see `docs/PHASE4.md`). Keep future services in clearly named top-level directories such as `mikrotik-collector/` and `agent-worker/`. Do not implement later roadmap phases unless the change explicitly targets them.

The web panel is a single-file vanilla SPA served by the backend at `backend/app/static/index.html`; there is no Next.js frontend and none is planned while the panel stays this size.

## Build, Test, and Development Commands

Standard commands:

- `cp .env.example .env` — create local configuration; never commit `.env`.
- `docker compose config` — validate the resolved Compose configuration.
- `docker compose up --build` — build and start the local stack.
- `docker compose ps` — verify service health.
- `pytest -q` — run backend tests from the configured Python environment.

Update `README.md` whenever these commands or prerequisites change.

## Coding Style & Naming Conventions

Use four-space indentation and PEP 8 conventions for Python. Comments and user-facing strings are written in Spanish; identifiers and docstring-free helpers follow the surrounding file. Name modules and functions with `snake_case`, classes with `PascalCase`, and constants with `UPPER_SNAKE_CASE`. Add type hints to public FastAPI and service interfaces. Use lowercase, hyphenated Docker service names. Keep configuration in environment variables and centralize settings rather than reading variables throughout business logic. Panel code stays dependency-free: no build step, no CDN, no external fonts (the Content-Security-Policy forbids them).

## Testing Guidelines

Place Python tests in `backend/tests/` and name files `test_<feature>.py`. Any endpoint that returns devices, backups or audit data must be covered in `test_multitenant.py`: a missing tenant filter leaks one customer's backups to another. Cover health endpoints, configuration validation, dependency failures, and read-only behavior. Tests must not require real MikroTik devices or production credentials; use fixtures and mocks. Every bug fix should include a regression test.

## Commit & Pull Request Guidelines

This folder has no Git history, so adopt Conventional Commits: `feat: add API health check`, `test: cover Redis outage`, or `docs: clarify ARM64 setup`. Keep commits focused. Pull requests should identify the roadmap phase, summarize behavior and security impact, list validation commands, link related issues, and include screenshots only for UI changes.

## Security & Architecture Constraints

Read all files in `docs/` before architectural changes. The deployment target is a Debian 13 x86_64 machine; prefer multi-arch images where practical. The panel is exposed through an external Nginx Proxy Manager that is not part of this repository: security headers and login rate limiting must stay in the application, not in proxy configuration. Keep Oxidized decoupled. Router access remains strictly read-only in early phases. AI output must never execute directly on routers. Do not commit credentials, backups, device exports, tokens, SNMP communities, or private keys.
