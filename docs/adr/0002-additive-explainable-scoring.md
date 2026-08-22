# 2. Additive, decomposable scoring

**Status:** Accepted · 2026-08-22

## Context

The draft score combines value over replacement, positional scarcity, roster
need, ADP value, next-pick urgency, availability risk, and bye collisions. These
could be combined additively, multiplicatively, or by a learned model.

The consumer is someone with seconds to decide whether to trust the number,
under pressure, mid-conversation.

## Decision

A weighted additive sum. Components are returned with every recommendation and
must sum to the headline score — asserted by tests at both the domain and API
layers.

## Alternatives considered

**Multiplicative.** Better at expressing "elite talent *and* huge need", but a
product is not decomposable into contributions, so the UI could only show
inputs, never their effect.

**Learned ranking model.** Probably better ranking quality. Rejected for the
top-level score: there is no ground truth for "correct pick", so it would be
fitted to a proxy, and its output could not be defended at the table. The place
for a learned model is the availability estimate, which produces one calibrated
probability that then enters this sum as a single term.

**Additive with a learned residual.** Rejected as the worst of both: the
explanation would omit exactly the part that changed the answer.

## Consequences

**Good.** Every number justifies itself. The UI renders the arithmetic without
interpretation. Tuning is legible — changing a weight has a predictable,
explainable effect. Regressions are obvious in a way a black box's are not.

**Bad.** Cannot express interactions. Weights are reasoned rather than fitted,
so "why 40 and not 35" has a defensible answer but not an empirical one. Almost
certainly leaves some ranking accuracy on the table.

That trade is accepted deliberately: a slightly worse recommendation that a
manager understands and can override is more useful than a slightly better one
they cannot interrogate.
