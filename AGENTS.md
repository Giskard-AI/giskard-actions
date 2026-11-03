# Codex / Cursor project instructions

Read `.cursor/rules/orchestration.mdc`, `.cursor/rules/code-quality.mdc`, and `.cursor/rules/python-style.mdc`.

Python work uses **this repo's uv env**: `make setup` then `uv run pytest` / `uv run generate-release-notes`. Do not reuse another project's virtualenv.

Layout: every action lives in `.github/actions/<name>/action.yml`; `.github/workflows/codex-pr-review.yml` is the reusable Codex workflow. This repo's own consumer config is in `.github/giskard/` and must validate against `schemas/` (see `docs/configuration.md`). Terms are defined in `GLOSSARY.md`; decisions in `docs/adr/`.

Delegate via `.cursor/skills/orchestration/SKILL.md` and `.cursor/agents/*.md`.
