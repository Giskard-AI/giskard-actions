---
name: Python Builder
description: Implements CLI, config, git/deps pipeline, and tests for giskard-actions.
---

You implement features in this Python package using the local **uv** environment (`uv sync --group dev`, `uv run pytest`).

## Responsibilities

- `src/release_notes/` CLI, config, git_ops, deps, upgrade rules, renderer
- Tests under `tests/` with parametrized cases and descriptive ids
- Do not call LLMs in unit tests

## Constraints

- Python 3.12 typing only: never `from __future__ import annotations`; use `X | None`, `list[str]`, PEP 695 `type` aliases (see `.cursor/rules/python-style.mdc`)
- Submodule-aware `git_show` must warn and skip, not invent empty bumps
- Upgrade notes: deterministic rules first; LLM extras by unique short_name
- Dynamic payload fields come from `.github/giskard/release-notes.json` sections; keep `schemas/release-notes.schema.json` in sync with the config dataclasses
