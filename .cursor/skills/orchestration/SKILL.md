# Orchestration Playbook

1. Identify files/domains (Python CLI, prompts, CI).
2. Dispatch the matching builder from `.cursor/agents/`.
3. After substantive changes, dispatch reviewers in parallel (see orchestration rule).
4. Run `uv sync --group dev` and `uv run pytest` in this repo.
