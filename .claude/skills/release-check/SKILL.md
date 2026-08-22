---
name: release-check
description: Pre-release verification across quality, security, data, docs, and the demo path. Use before tagging a release or handing the repository to a reviewer.
---

# Release check

Everything that must be true before this repository is handed to someone else.

## 1. Gates

```bash
make quality
```

## 2. The demo path, actually walked

The acceptance criteria are about the demo experience, so walk it rather than
assuming it:

```bash
make dev-api        # terminal one
make dev-web        # terminal two
```

Then: open the app, start a draft, advance to your pick, draft a player, and
confirm the roster fills, the ticker shows your pick, recommendations change,
and the drawer opens with an injury timeline. If any step needs a reload, the
live update path is broken.

## 3. Security

```bash
git log -p | grep -inE '(api[_-]?key|secret|password|token)\s*[:=]\s*["\x27][^"\x27]{8,}' | head
./.venv/bin/pip-audit --strict
npm audit --audit-level=high
```

Confirm `.env` is ignored, `.env.example` is current, no secret sits behind a
`NEXT_PUBLIC_` prefix, and the GPL-licensed crosswalk is not committed.

## 4. Data honesty

- Does every displayed metric carry a source and a timestamp?
- Is synthetic demo data labelled as synthetic everywhere it appears?
- Does `docs/DATA_SOURCES.md` still match reality? If unsure, run the
  `data-source-audit` skill.

## 5. Documentation truth

Read the README as someone who has never seen the project. Does it describe what
exists, or what was intended? Every claim about a feature must be traceable to
code that runs. Remove or clearly mark anything aspirational.

## 6. Repository hygiene

```bash
git status --short
du -sh .git
```

No stray build output, no committed caches, no `node_modules`. Check the commit
history reads as a coherent narrative and carries no AI attribution.
