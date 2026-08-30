# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a documentation-first starter. `README.md` explains the intended bootstrap workflow, while `docs/` contains product context, architecture, agent boundaries, roadmap, and security requirements. `.env.example` documents non-secret configuration defaults.

Phase 1 should introduce `docker-compose.yml`, a FastAPI service under `backend/`, and configuration for PostgreSQL, Redis, and Oxidized. Keep future services in clearly named top-level directories such as `frontend/`, `mikrotik-collector/`, and `agent-worker/`. Do not implement later roadmap phases unless the change explicitly targets them.

## Build, Test, and Development Commands

There is no executable stack yet. Phase 1 contributions must establish and document these standard commands:

- `cp .env.example .env` — create local configuration; never commit `.env`.
- `docker compose config` — validate the resolved Compose configuration.
- `docker compose up --build` — build and start the local stack.
- `docker compose ps` — verify service health.
- `pytest -q` — run backend tests from the configured Python environment.

Update `README.md` whenever these commands or prerequisites change.

## Coding Style & Naming Conventions

Use four-space indentation and PEP 8 conventions for Python. Name modules and functions with `snake_case`, classes with `PascalCase`, and constants with `UPPER_SNAKE_CASE`. Add type hints to public FastAPI and service interfaces. Use lowercase, hyphenated Docker service names. Keep configuration in environment variables and centralize settings rather than reading variables throughout business logic. Frontend code should follow the formatter and linter introduced with Next.js.

## Testing Guidelines

Place Python tests in `backend/tests/` and name files `test_<feature>.py`. Cover health endpoints, configuration validation, dependency failures, and read-only behavior. Tests must not require real MikroTik devices or production credentials; use fixtures and mocks. Every bug fix should include a regression test.

## Commit & Pull Request Guidelines

This folder has no Git history, so adopt Conventional Commits: `feat: add API health check`, `test: cover Redis outage`, or `docs: clarify ARM64 setup`. Keep commits focused. Pull requests should identify the roadmap phase, summarize behavior and security impact, list validation commands, link related issues, and include screenshots only for UI changes.

## Security & Architecture Constraints

Read all files in `docs/` before architectural changes. The deployment target is a Debian 13 x86_64 machine; prefer multi-arch images where practical. Keep Oxidized decoupled. Router access remains strictly read-only in early phases. AI output must never execute directly on routers. Do not commit credentials, backups, device exports, tokens, SNMP communities, or private keys.
