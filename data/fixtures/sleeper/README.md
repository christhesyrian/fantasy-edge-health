# Sleeper contract fixtures

**Fixture data — synthetic values, real schema.**

These files reproduce the exact field names, types, and nullability observed in
live Sleeper API responses on **2026-08-22**, with all identifiers, usernames,
and player names replaced by synthetic values. No real account or league data is
stored here.

They exist so the provider's parsing contract can be tested without network
access. If Sleeper changes a payload shape, the contract tests keep passing
while production breaks — so these files carry a `_verified_on` marker and
`docs/DATA_SOURCES.md` records when they were last checked against the live API.

Schema quirks deliberately preserved because the adapter must handle them:

- `roster_id` on a draft pick is documented as a string but arrives as an
  **integer**. `picks.json` uses integers; `picks_string_roster_id.json` uses
  the documented strings. Both must parse.
- Draft picks carry an undocumented `reactions` field.
- `is_keeper` is `null` rather than `false` when unset.
- NFL state returns more fields than the documentation lists.
