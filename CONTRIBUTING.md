# Contributing to Torqrun

Thanks for your interest! The project is pre-alpha; architecture is documented in
[docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) — please read the
relevant section before proposing large changes, and open an issue first for anything
that changes the agent protocol, database schema or run state machine.

## Licensing of contributions

Torqrun is open core: this repository (the Community Edition) is Apache-2.0, and paid plans
are services built around it ([docs/editions.md](docs/editions.md)). Contributions are
accepted under the same Apache-2.0 license (inbound = outbound, see section 5 of the
license). There's no CLA. By opening a pull request you confirm you have the right to
contribute the code.

## Workflow

1. Set up the toolchain: [docs/development/getting-started.md](docs/development/getting-started.md).
2. Branch from `main`; keep pull requests focused on one change.
3. `make check` must pass locally (lint, mypy --strict, tests, web checks).
4. Add tests for behaviour changes. Don't weaken or skip existing tests to make a build pass.
5. Database changes need an Alembic migration with a working `downgrade`.
6. Update docs and `CHANGELOG.md` (under *Unreleased*) for user-visible changes.

## Conventions

- Python 3.12, type hints everywhere, `ruff` formatting (line length 100).
- Configuration through `TORQRUN_*` environment variables; never commit secrets.
- Never execute user-supplied code in the API or scheduler processes.
- Commit messages: imperative mood, short subject (`Add run lease reaper`), body explains *why*.

By contributing you agree that your contributions are licensed under Apache-2.0 and that you
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
