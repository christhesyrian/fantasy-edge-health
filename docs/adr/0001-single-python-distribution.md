# 1. Single Python distribution with an enforced purity boundary

**Status:** Accepted · 2026-08-22

## Context

The build directive proposed a monorepo with separate packages under
`services/` and `packages/`, each presumably its own installable distribution.
The goal behind that structure is a hard boundary between the pure domain and
everything that performs I/O.

That boundary genuinely matters here. If the draft engine can reach a database,
it stops being testable without one, the simulator stops being a real rehearsal
of the live path, and reasoning about a recommendation requires tracing queries.

## Decision

One Python distribution, `src/fhe/`, with submodules `core`, `data`, `db`,
`api`, `worker`, `ml`. The purity boundary is enforced by
`tests/architecture/test_core_purity.py`, which walks the AST of every module
under `fhe.core` and fails on a forbidden import.

`services/api/` and `services/worker/` remain as deployment units with their own
Dockerfiles.

## Alternatives considered

**Separate installable packages.** Rejected: four `pyproject.toml` files, four
editable installs, dependency resolution between them, and a slower feedback
loop — in exchange for a boundary the test already enforces more precisely.

**Convention only, no enforcement.** Rejected: an unenforced rule is a rule that
erodes. The first time someone needs "just one query" in the engine, it goes in.

## Consequences

**Good.** One dependency set, one virtualenv, one mypy run. The AST test is
*stricter* than package boundaries — it can forbid `os` and `pathlib`, which a
package boundary cannot. Violations are named at the exact module.

**Bad.** Nothing physically prevents an import; the test is the only guard, so
it must never be skipped. A future consumer wanting only the engine would have
to install the whole distribution.

**Revisit if** the engine is ever published for external use, or if the codebase
grows past what one distribution can sensibly hold.
