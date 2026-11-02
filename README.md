# giskard-actions (repo: `Giskard-AI/giskard-release-notes`)

Private Giskard GitHub Actions repo. Intended GitHub name: **`Giskard-AI/giskard-actions`**. Pin consumers to a **40-character commit SHA**.

| Product | Form | Consumer leftover |
| --- | --- | --- |
| Release notes | Composite `action.yml` + CLI `generate-release-notes` | `.release-notes.json` + thin workflow |
| Codex PR review | `.github/actions/codex-pr-review` + `codex-post-review` | `.github/codex/reviewer-map.json` + shim jobs |

Trusted automation is the pinned unpack (`github.action_path`). Callers cannot retarget the helper. Codex overlays load from the **consumer PR base SHA**, not PR head. Consumers own `environment:`, job `if:`, and permissions.

## Release notes

Standalone CLI and composite action that generates structured, LLM-powered release notes from git history. Uses **giskard-agents** for sandboxed Jinja prompts and **giskard-llm** for Azure completions.

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
make lock-upgrade   # uv lock -U (latest versions allowed by pyproject.toml)
```

CI keeps `uv.lock` current in two ways: **Update lockfiles** (weekly Monday 06:00 UTC, or Actions → workflow_dispatch) opens a `chore: update lockfiles` PR against `main`. Renovate pep621 PRs get the `deps:lockfile=python` label; **Renovate lockfile refresh** then runs `uv lock -U` and pushes a follow-up commit on that branch. Manual re-lock: add the label or dispatch the refresh workflow with a branch name. Those jobs mint a short-lived GitHub App installation token (`vars.AUTOMATION_APP_ID` as the numeric App ID + `secrets.AUTOMATION_APP_PRIVATE_KEY`) so follow-up Tests run; the default `GITHUB_TOKEN` does not retrigger workflows.

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

Initing lidar is **not** required to *run* this action (it `uv tool install`s itself from a lockfile export). It is only required to *diff* files inside that submodule.

## Codex PR review (composite)

Trusted helper code is this repo at the pinned SHA (`github.action_path`). Reviewer maps and agent overlays load from the consumer **base SHA**. Put `environment: ci` on the analyze job only. Post with `pull-requests: write` and no Azure.

In the **consumer** repo:

1. Copy `examples/llm-hub.reviewer-map.json` to `.github/codex/reviewer-map.json` (adjust paths). Use `role_references` basenames only.
2. Keep `.cursor/agents/<name>.md` and optional `.cursor/agents/references/` on the default branch.
3. Add thin jobs. Never use `secrets: inherit`. Never `python3 scripts/ci/` from the consumer workspace. Pin the **same SHA** as notes.

```yaml
name: Codex PR Code Review
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled]
  issue_comment:
    types: [created]
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request number to review
        required: true
        type: string
permissions: {}
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.event.issue.number || inputs.pr_number || github.run_id }}
  cancel-in-progress: ${{ github.event_name != 'issue_comment' }}
jobs:
  review:
    if: >-
      (
        github.event_name == 'pull_request' &&
        contains(github.event.pull_request.labels.*.name, 'codex-review')
      ) ||
      github.event_name == 'workflow_dispatch' ||
      (
        github.event_name == 'issue_comment' &&
        github.event.issue.pull_request &&
        contains(fromJSON('["/review","/codex-assess"]'), github.event.comment.body) &&
        contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)
      )
    runs-on: ubuntu-latest
    timeout-minutes: 120
    environment:
      name: ci
      deployment: false
    permissions:
      contents: read
      issues: read
      pull-requests: read
    outputs:
      should_run: ${{ steps.codex.outputs.should_run }}
      pr_number: ${{ steps.codex.outputs.pr_number }}
    steps:
      - id: codex
        uses: Giskard-AI/giskard-release-notes/.github/actions/codex-pr-review@<sha>
        with:
          responses_api_base: ${{ vars.AZURE_AI_RESPONSES_BASE }}
          azure_ai_api_key: ${{ secrets.AZURE_AI_API_KEY }}
          github_token: ${{ github.token }}
  post:
    needs: review
    if: needs.review.result == 'success' && needs.review.outputs.should_run == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/download-artifact@<sha>
        with:
          name: codex-reviewer-outputs
          path: .
      - uses: Giskard-AI/giskard-release-notes/.github/actions/codex-post-review@<sha>
        with:
          github_token: ${{ github.token }}
          pr_number: ${{ needs.review.outputs.pr_number }}
```

Start a review with the `codex-review` label, `/review`, or `/codex-assess` from OWNER/MEMBER/COLLABORATOR.

Codex uses **`AZURE_AI_RESPONSES_BASE`** (Responses API). Release notes use **`AZURE_AI_API_BASE`** (giskard-llm). Do not collapse those URLs.

Org: allow this private repo's actions for each consumer. Codex unpack does not need an Automation App. zizmor's `impostor-commit` check may.

## Cursor

This repo has a small agent roster under `.cursor/` (python-builder, prompt-engineer/critic, devops-helper, infra-reviewer, security-auditor). See `AGENTS.md`.
