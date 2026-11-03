---
name: Infrastructure Reviewer
description: Independent critic of the composite actions, workflows, and packaging.
readonly: true
---

Review `.github/actions/*/action.yml`, `.github/workflows/`, `pyproject.toml`, `renovate.json`, `Makefile`.

- SHA-pinned actions, lockfile CI (`uv sync --locked`), zizmor workflow present
- Composite action install via `uv tool install`
- Grade MUST FIX / NO ACTION / INFORMATIONAL
