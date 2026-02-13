# giskard-release-notes

Standalone CLI tool that generates structured, LLM-powered release notes from git history. Replaces GitHub's auto-generated "What's Changed" PR lists with categorized, human-readable summaries.

Works with any git repository. Designed to be used locally or as a reusable GitHub Actions workflow.

## How it works

1. Detects the tag range (e.g. `v2.2.1..v2.3.0`)
2. Extracts per-commit diffs, prioritizing high-signal paths
3. Fetches Linear issues referenced in commits (optional)
4. Calls an LLM to consolidate each commit into a human description
5. Calls the LLM again to synthesize all consolidations into structured release notes
6. Renders a markdown file with configurable sections

## Quick start

```bash
# Install
uv sync

# Dry run (no LLM calls, good for testing the pipeline)
uv run generate-release-notes \
  --version 2.3.0 \
  --repo /path/to/your-repo \
  --output output/v2.3.0.md \
  --dry-run

# Full run with LLM (needs API keys)
uv run generate-release-notes \
  --version 2.3.0 \
  --repo /path/to/your-repo \
  --output output/v2.3.0.md \
  --env-file /path/to/your-repo/.env
```

## Configuration

### API keys

The tool needs LLM API keys. You can provide them via:

- Environment variables (`AZURE_AI_API_KEY`, `AZURE_AI_API_BASE`, `AZURE_API_VERSION`)
- `--env-file` flag pointing to a `.env` file that contains them
- Linear enrichment (optional): `LINEAR_API_KEY`

### Per-project config (`.release-notes.json`)

Place a `.release-notes.json` at the root of the target repository to configure:

```json
{
  "github_repository": "Giskard-AI/llm-hub",
  "manifest_paths": ["backend/pyproject.toml", "frontend/package.json"],
  "high_signal_prefixes": ["backend/src/backend/routers/", "frontend/src/app/"],
  "ignored_basenames": ["uv.lock", "pnpm-lock.yaml"],
  "sections": [
    { "id": "summary" },
    { "id": "features" },
    { "id": "fixes" },
    { "id": "ux_frontend", "enabled": false },
    { "id": "api_backend" },
    { "id": "upgrade_notes" },
    { "id": "deps" },
    { "id": "appendix" }
  ]
}
```

| Field                  | Description                                                              |
| ---------------------- | ------------------------------------------------------------------------ |
| `github_repository`    | GitHub `owner/repo` slug for compare URLs                                |
| `manifest_paths`       | Dependency files to track for version bumps                              |
| `high_signal_prefixes` | Path prefixes to prioritize in diff extraction (ordered by priority)     |
| `ignored_basenames`    | File names to exclude from diffs (e.g. lock files)                       |
| `sections`             | Which sections to include in the output (set `"enabled": false` to skip) |

### Available sections

| Section ID      | Description                                  |
| --------------- | -------------------------------------------- |
| `summary`       | High-level release summary                   |
| `features`      | New capabilities                             |
| `fixes`         | Bug fixes                                    |
| `ux_frontend`   | UI/UX changes                                |
| `api_backend`   | API/backend changes                          |
| `infra_deps`    | Infrastructure and CI changes                |
| `upgrade_notes` | Migrations, config changes, breaking changes |
| `deps`          | Dependency version bump table                |
| `appendix`      | Diff stats, commit list, per-commit details  |

## CLI options

```
--version         Release version (required, e.g. 2.3.0)
--output          Output markdown path (required)
--repo            Path to the git repository (default: current directory)
--model           LiteLLM model name (default: azure_ai/gpt-4.1)
--previous-tag    Override previous tag detection
--env-file        Path to a .env file to load
--dry-run         Skip LLM/Linear calls, produce debug output
--llm-concurrency Max concurrent LLM calls (default: 10)
--log-level       DEBUG, INFO, WARNING, ERROR (default: INFO)
```
