---
name: DevOps Helper
description: GitHub Action, workflows, Renovate, Makefile, and uv packaging for giskard-actions.
---

- Pin third-party actions to full SHA with version comments; same-repo references (`./path`, `$/path`) are not pinned
- `permissions: {}` at workflow top; job-scoped grants
- `${{ }}` only via `env:` intermediaries in `run:` blocks
- Passing a secret to a declared action input (`with:`) or a reusable workflow's `secrets:` is fine; never interpolate it in `run:`
- Python deps via `uv lock` / `uv sync --group dev --locked` in this repo
