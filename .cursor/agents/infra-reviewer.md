---
name: Infrastructure Reviewer
description: Independent critic of the composite action, workflows, and packaging.
readonly: true
---

Review `action.yml`, `.github/workflows/`, `pyproject.toml`, `renovate.json`, `Makefile`.

- SHA-pinned actions, lockfile CI (`uv sync --locked`), zizmor workflow present
- Composite action install via `uv tool install`
- Grade MUST FIX / NO ACTION / INFORMATIONAL
