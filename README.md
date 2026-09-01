# giskard-release-notes

Standalone CLI and GitHub composite action that generates structured, LLM-powered release notes from git history. Uses **giskard-agents** for sandboxed Jinja prompts and **giskard-llm** for Azure completions.

## How it works

1. Detects the tag range (e.g. `v2.2.1..v2.3.0`)
2. Extracts per-commit diffs, prioritizing high-signal paths from `.release-notes.json`
3. Optionally fetches Linear issues and GitHub PRs/issues
4. Consolidates each commit via giskard-agents structured output
5. Synthesizes notes into the **sections declared in `.release-notes.json`**
6. Applies **deterministic upgrade_rules** (git path matches), then keeps extra LLM upgrade bullets
7. Renders markdown (optional appendix)

## Local setup (uv)

```bash
make setup          # uv sync --group dev
make test           # uv run pytest
make format
make check_linting
```

Or:

```bash
uv sync --group dev
uv run generate-release-notes --help
```

### Dry run (no LLM)

```bash
uv run generate-release-notes \
  --version 2.3.0 \
  --repo /path/to/your-repo \
  --output output/v2.3.0.md \
  --dry-run
```

### Full run

Copy `.env.example` and set Azure keys. Then:

```bash
uv run generate-release-notes \
  --version 2.3.0 \
  --repo /path/to/your-repo \
  --output output/v2.3.0.md \
  --env-file .env
```

Required for Azure models (`azure_ai/…`): `AZURE_AI_API_KEY` and `AZURE_AI_ENDPOINT` (or `AZURE_AI_API_BASE`). Optional: `LINEAR_API_KEY`, `GITHUB_TOKEN`.

Default model: `azure_ai/gpt-5.4`.

## GitHub Action

Consumers pass secrets on the **step `env:`**, never in `with:`:

```yaml
permissions: {}
jobs:
  notes:
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@… # fetch-depth: 0, fetch-tags: true
        # If manifest_paths include files inside a git submodule:
        # with:
        #   submodules: true
      - uses: Giskard-AI/giskard-release-notes@<sha>
        with:
          version: "3.0.0"
          include_appendix: "true"
        env:
          AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
          AZURE_AI_API_BASE: ${{ vars.AZURE_AI_API_BASE }}
          AZURE_AI_ENDPOINT: ${{ vars.AZURE_AI_API_BASE }}
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY_READONLY }}
          GITHUB_TOKEN: ${{ github.token }}
```

Pin the action to a full commit SHA. Enable the **Renovate** GitHub App on this repo so action digests and Python deps stay current.

## `.release-notes.json`

Place at the **target repo** root (not this tool repo). See `examples/`.

| Field | Meaning |
| --- | --- |
| `github_repository` | `owner/repo` for compare URLs |
| `manifest_paths` | pyproject.toml / package.json / pom.xml to diff for the deps table |
| `high_signal_prefixes` | Paths that get per-file diffs in commit digests |
| `file_diffs` | Paths whose unified tag-range diff is pasted into the markdown |
| `ignored_basenames` / `ignored_path_prefixes` | Filter lockfiles and noise from stats |
| `sections` | Output contract: `id`, optional `title`, `description` (LLM writing instructions), `enabled` |
| `upgrade_rules` | Git-only upgrade bullets (`paths`, `path_prefixes`, optional `filename_keywords`) |

Special section ids (not sent to the LLM): `deps`, `appendix`. `upgrade_notes` is hybrid: rules always apply, LLM extras are kept if their `short_name` is new.

Section lists differ by product. llm-hub uses UX/API/Helm; lidar uses `python_api` and no frontend/Helm. Copy `examples/llm-hub.release-notes.json` or `examples/lidar.release-notes.json`.

## Submodules / vendor manifests

`git show vX:backend/vendor/lidar/pyproject.toml` does **not** work when lidar is a **gitlink**. This tool:

1. Detects mode `160000` gitlinks on the parent tag
2. Reads the nested file at the gitlink SHA from the submodule checkout

**Consumers** that list submodule paths in `manifest_paths` must init those submodules **before** the action (`actions/checkout` `submodules: true`, or a GitHub App token checkout). If objects are missing, the tool logs a warning and skips that manifest instead of reporting an empty bump list.

Initing lidar is **not** required to *run* this action (it `uv tool install`s itself). It is only required to *diff* files inside that submodule.

## Cursor

This repo has a small agent roster under `.cursor/` (python-builder, prompt-engineer/critic, devops-helper, infra-reviewer, security-auditor). See `AGENTS.md`.
