---
name: quality-gate
description: Run the full definition-of-done gate — format, lint, type check, and every test suite — and fix what fails. Use before committing, before declaring a phase complete, or when asked whether the project is green.
---

# Quality gate

The project's definition of done. A change is not complete until all of this
passes, and a test is never disabled to get there.

## Run it

```bash
make quality
```

That runs, in order: `ruff format`, `ruff check`, `mypy`, `pytest`, then the
frontend's prettier, eslint, `tsc`, and vitest.

To run pieces individually:

```bash
./.venv/bin/ruff format --check src tests
./.venv/bin/ruff check src tests
./.venv/bin/mypy
./.venv/bin/python -m pytest -q
npm run lint && npm run typecheck && npm run test
```

## When something fails

Fix the cause, not the symptom.

- **A lint rule fires.** Ask what it is protecting against before suppressing
  it. `ARG001` on an unused parameter usually means the parameter is dead.
  `ERA001` on a comment usually means the comment reads like code and should be
  reworded. A `noqa` needs a comment saying why.
- **mypy complains about a library.** Check whether the stubs are stale before
  adding an ignore. A targeted `# type: ignore[code]` with a one-line reason is
  acceptable; a blanket module override is usually not.
- **A test fails.** Determine whether the test or the code is wrong. If the
  behaviour genuinely changed, update the test *and* say so in the commit body.
  Never weaken an assertion to pass.
- **Migration drift.** `tests/integration/test_migrations.py` failing means a
  model changed without a revision. Generate one:
  `make migration m="describe the change"`.

## Before committing

```bash
git diff --cached | grep -inE '(api[_-]?key|secret|password|token)\s*[:=]\s*["\x27][^"\x27]{8,}'
```

Then check the diff for debugging artefacts, commented-out code, and anything
that would credit an AI assistant — this project does not.
