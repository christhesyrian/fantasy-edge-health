# Fantasy Health Edge — engineering conventions

Persistent rules for working in this repository. This file is only what must
always be true; it is checked in because it governs the code, not the tooling.

The governing specification is [`docs/MASTER_BUILD_DIRECTIVE.md`](docs/MASTER_BUILD_DIRECTIVE.md).
Current state and the ordered backlog are in [`HANDOFF.md`](HANDOFF.md).
Frontend boundaries and the API contract are in
[`docs/V0_HANDOFF.md`](docs/V0_HANDOFF.md).

## Environment

- **Always invoke `./.venv/bin/python`.** Bare `python3` on this machine is
  Anaconda 3.9 and will fail. The venv is Python 3.14.
- Frontend commands run from the repo root via npm workspaces (`npm run dev`).
- `make help` lists every common operation.

## Architecture rules

1. **`src/fhe/core/` is pure.** No database, no HTTP, no filesystem, no clock
   that is not injected. Enforced by `tests/architecture/test_core_purity.py`,
   which walks the AST of every core module. If a change needs I/O in core, the
   design is wrong — move the I/O out, not the test.
2. **Business rules live in the domain, never in a router or a component.** The
   live draft, the simulator, and the tests must all reach the same answer
   through the same code.
3. **Depend on narrow Protocols, not concrete providers.** An ingestion job that
   needs one method declares a Protocol with one method.
4. **The frontend computes nothing.** It renders what the engine returned. No
   recommendation logic in TypeScript.

## Data rules

5. **Never invent an external fact.** Endpoints, fields, rate limits, licences,
   and availability dates are verified against current documentation *and* live
   responses before use, then recorded in `docs/DATA_SOURCES.md` with the date.
   If it cannot be confirmed, label it unknown and leave the adapter disabled.
6. **Never discard raw provider text.** Store the original beside every
   normalised value, so a mapping bug is fixable by re-running normalisation.
7. **A corrupt or empty provider response must never overwrite good state.**
   Ingestion jobs carry plausibility floors and refuse rather than degrade.
8. **Malformed records are counted and sampled, never silently dropped.**
9. **Ambiguity is recorded, not guessed.** An unresolvable player becomes a row
   in `player_identity_conflicts`.
10. **Ingestion is idempotent.** Re-running converges. Uniqueness constraints
    are the guarantee; note that a nullable column in a unique key does *not*
    deduplicate, which is why `week` uses the `SEASON_LONG_WEEK` sentinel.

## Product rules

11. **Every score is decomposable.** Components must sum to the headline, and
    tests assert it. Never present an opaque number.
12. **Explanations come from structured facts.** No LLM writes a recommendation
    or its reasons. The product must work with no LLM key at all.
13. **Missing data lowers confidence; it never invents risk or value.** An
    unmeasured player is unknown, not safe.
14. **Language discipline.** "Elevated availability risk", never "will get
    injured". Every health payload carries its own limitations.
15. **Degradations are always visible.** SQLite and the in-process bus are
    legitimate fallbacks, and the health endpoint says so out loud.

## Code style

- Typed Python, `mypy --strict` clean. TypeScript strict, no `any`.
- Named constants with a comment saying why that value. No inline magic numbers.
- No bare `except` (enforced by a test), no `print` outside the CLI, no silent
  failure, no TODO without an explanation, no dead code.
- Docstrings explain *why*, not what the code already says.
- Comments earn their place by explaining a non-obvious decision or trap.

## Definition of done

A change is complete when `make quality` passes: format, lint, type check, and
every suite. Never disable a test to get green.

## Git

- Coherent commits with a body explaining the reasoning, not just the change.
- **No AI attribution anywhere** — no `Co-Authored-By` trailers, no "generated
  with" footers, no such notes in source or docs.
- Never commit secrets, `.env`, provider dumps, or the GPL-licensed crosswalk.
