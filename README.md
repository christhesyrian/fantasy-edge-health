# Fantasy Health Edge

**Injury-adjusted fantasy football draft intelligence with a live draft war room.**

Fantasy Health Edge answers one question, continuously, while your draft is running:

> *Who should I draft right now, after accounting for expected production, availability
> risk, roster construction, positional scarcity, ADP value, and the probability this
> player survives until my next pick?*

> **Status:** under active construction. See [`PROGRESS.md`](PROGRESS.md) for the live build log
> and [`USER_ACTION_REQUIRED.md`](USER_ACTION_REQUIRED.md) for anything that needs you.

---

## What makes it different

- **Availability risk, not injury prediction.** The system estimates *fantasy-relevant
  availability risk* from historical football data. It never claims a player will get hurt.
  See [`docs/INJURY_MODEL.md`](docs/INJURY_MODEL.md).
- **Every score is decomposable.** No opaque numbers. A `91.4` always comes with the
  component breakdown that produced it.
- **The engine is pure.** Recommendation logic lives in `src/fhe/core/`, has zero I/O, and is
  driven identically by the live Sleeper draft, the mock simulator, and the unit tests.
- **Honest about data.** Every metric carries its provider and timestamp. Nothing is
  fabricated when a source is unavailable; it degrades and says so.

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design and data flow |
| [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) | Every external source, verified, with limitations |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Database schema |
| [`docs/DRAFT_ENGINE.md`](docs/DRAFT_ENGINE.md) | The recommendation mathematics |
| [`docs/INJURY_MODEL.md`](docs/INJURY_MODEL.md) | Health risk features and limits |
| [`docs/adr/`](docs/adr/) | Architecture decision records |

## License

MIT — see [`LICENSE`](LICENSE).
