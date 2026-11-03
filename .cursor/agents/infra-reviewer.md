---
name: Infrastructure Reviewer
description: Independent critic of the composite actions, workflows, and packaging.
readonly: true
---

Review `.github/actions/*/action.yml`, `.github/workflows/`, `pyproject.toml`, `renovate.json`, `Makefile`.

- Third-party actions pinned to a full SHA with a version comment. Same-repo references (`./path`, `$/path`) are valid and are not pinned.
- Lockfile CI (`uv sync --locked`), zizmor workflow present
- Composite action install via `uv tool install`
- Grade MUST FIX / NO ACTION / INFORMATIONAL
