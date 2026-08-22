---
name: draft-simulation
description: Exercise the recommendation engine through mock drafts to validate a scoring change. Use after touching weights, VORP, scarcity, survival probability, or roster need.
---

# Draft simulation

The engine is deterministic, so a seeded mock draft is a repeatable experiment
rather than a demo. Use it to see whether a change actually improved anything.

## Quick look

```bash
./.venv/bin/python -m fhe.cli simulate --seed 42
```

Prints the board at the user's first pick with the full component breakdown.

## Sweep league shapes

A change that helps a 12-team PPR league can quietly break superflex. Check
several:

```bash
for seed in 1 7 42 99; do
  ./.venv/bin/python -m fhe.cli simulate --seed $seed --teams 12 | head -6
done
./.venv/bin/python -m fhe.cli simulate --teams 10
./.venv/bin/python -m fhe.cli simulate --teams 14 --slot 14
```

## What to look for

Read the board as a drafter would, and treat anything indefensible as a bug:

- A kicker or defense anywhere near the early rounds. This has happened twice
  here, once from a missing replacement baseline and once from normalising VORP
  against the shrinking available pool.
- A round-14 player scoring like the 1.01.
- A quarterback ranked identically in single-QB and superflex.
- Survival probability near zero for a player who has already fallen well past
  their ADP.
- Components that do not sum to the headline score.

## Full-draft behaviour

`tests/unit/test_simulator.py` drives a complete draft with the engine picking
for the user, then asserts the resulting roster is legal. Run it after any
scoring change:

```bash
./.venv/bin/python -m pytest tests/unit/test_simulator.py -q
```

If a tuning change makes that roster illegal — no tight end, no quarterback —
the change has broken roster need, however good the top of the board looks.
