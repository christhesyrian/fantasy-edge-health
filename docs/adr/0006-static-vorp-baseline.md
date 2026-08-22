# 6. Static pre-draft VORP baseline

**Status:** Accepted · 2026-08-22

## Context

Value over replacement needs a baseline. It can be computed once from the
pre-draft pool, or recomputed continuously against the players still available.

A dynamic baseline sounds strictly better: as running backs come off the board,
replacement level falls, and the remaining backs become more valuable. That is a
real effect.

## Decision

Compute the baseline **once, from the full pre-draft pool**. Model draft dynamics
through the separate positional-scarcity and next-pick-survival terms.

The normalisation scale is also fixed pre-draft, held on
`ReplacementBaseline.max_vorp`.

## Alternatives considered

**Dynamic baseline (VONA/VOLS style).** Rejected for double-counting: the engine
already models the draft emptying through scarcity and survival probability.
Folding the same effect into VORP as well over-weights positional runs, and the
two terms become impossible to reason about independently.

**Hybrid, dynamic late.** Rejected as extra complexity with an unclear boundary
and no evidence it helps.

## Consequences

**Good.** VORP is a stable, interpretable measure of talent. Scores are
comparable across the whole draft rather than only within a pick. The
explanation stays legible: "176 points above the RB29 baseline" means the same
thing at pick 1 and pick 100.

**Bad.** Slower to react to an extreme positional run than a dynamic baseline
would be — mitigated by scarcity, which is explicitly designed to catch it.

**This decision was validated by a bug.** Normalising against the *available*
pool meant the best remaining player always scored the full value weight, so a
round-14 defense scored like the 1.01. Pinned by
`test_late_round_positions_surface_at_the_end` and the fixed `max_vorp` scale.
