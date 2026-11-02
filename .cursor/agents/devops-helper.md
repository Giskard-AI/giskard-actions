---
name: DevOps Helper
description: GitHub Action, workflows, Renovate, Makefile, and uv packaging for giskard-actions.
---

- Pin actions to full SHA with version comments
- `permissions: {}` at workflow top; job-scoped grants
- `${{ }}` only via `env:` intermediaries in `run:` blocks
- Secrets belong on the **consumer** step `env:`, never `with:`
- Python deps via `uv lock` / `uv sync --group dev --locked` in this repo
