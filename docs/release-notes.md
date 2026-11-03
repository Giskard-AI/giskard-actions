---
title: Release notes
nav_order: 2
---

# Release notes
{: .no_toc }

A composite action and CLI (`generate-release-notes`) that turns the commits between two tags into structured Markdown release notes.

1. TOC
{:toc}

## How it works

1. Detects the tag range (for example `v1.1.0..v1.2.0`). In dry-run mode a missing target tag falls back to `HEAD`.
2. Builds a digest per commit, prioritizing diffs under `high_signal_prefixes`.
3. Optionally fetches referenced Linear issues (`LINEAR_API_KEY`) and GitHub issues and pull requests (`GITHUB_TOKEN`). GitHub issues and pull requests are used only when their author is an owner, member or collaborator of the repository; others are skipped with a log line.
4. Consolidates each commit with an LLM into one category matching your configured sections.
5. Synthesizes the configured sections, then applies deterministic `upgrade_rules` and keeps extra LLM upgrade notes with new names.
6. Sanitizes model-written text: drops images, raw HTML and invisible characters, and removes links that do not point to `https://github.com/<your repository>/`.
7. Renders Markdown, a dependency bump table from `manifest_paths`, raw diffs of `file_diffs`, and an optional appendix. Links the tool writes itself (compare URL, commit and issue links) are kept.

Prompts use [giskard-agents](https://pypi.org/project/giskard-agents/) sandboxed templates; completions go through [giskard-llm](https://pypi.org/project/giskard-llm/). The default model is `azure_ai/gpt-5.6-luna` with `high` reasoning effort.

## Usage

{% raw %}
```yaml
permissions: {}

jobs:
  release-notes:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          fetch-tags: true
          persist-credentials: false
          # submodules: true  # if manifest_paths point into submodules

      - id: notes
        uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<full-commit-sha> # v0.4.0
        with:
          version: "1.2.0"
          include_appendix: "true"
        env:
          AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
          AZURE_AI_API_BASE: ${{ vars.AZURE_AI_API_BASE }}
          AZURE_AI_ENDPOINT: ${{ vars.AZURE_AI_API_BASE }}
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }} # private repositories only
          GITHUB_TOKEN: ${{ github.token }}
```
{% endraw %}

Pass credentials on the step `env:`.

### Linear enrichment

With `LINEAR_API_KEY` set, the tool fetches the title and description of every Linear issue referenced in the commit range and gives them to the model, which can paraphrase or quote them in the notes. The notes then become a GitHub Release that anyone who can read the repository can see.

- **Public repositories:** do not set `LINEAR_API_KEY`. Ticket text (customer names, internal plans, security details) would be published.
- **Private repositories:** use a Linear key restricted to teams whose tickets are safe for everyone with read access to the repository.

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_AI_API_KEY` | For `azure_ai/` models | Azure AI key |
| `AZURE_AI_API_BASE` / `AZURE_AI_ENDPOINT` | For `azure_ai/` models | Azure AI endpoint (`https://<resource>.services.ai.azure.com/api/projects/<project>`) |
| `LINEAR_API_KEY` | No | Enables Linear issue enrichment for bracketed keys like `[ABC-123]` in commit messages. Private repositories only; see [Linear enrichment](#linear-enrichment) |
| `GITHUB_TOKEN` | No | Enables GitHub issue and pull request enrichment (owner, member and collaborator authors only) |

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `version` | (required) | Release version, e.g. `1.2.0`; the tag `v1.2.0` must exist unless `dry_run` |
| `previous_tag` | auto-detect | Override the previous tag |
| `model` | config or `azure_ai/gpt-5.6-luna` | Default model |
| `effort` | config or `high` | Default reasoning effort |
| `consolidation_model` / `consolidation_effort` | `model` / `effort` | Overrides for per-commit consolidation |
| `synthesis_model` / `synthesis_effort` | `model` / `effort` | Overrides for synthesis |
| `llm_concurrency` | config or `10` | Maximum parallel LLM calls |
| `dry_run` | `false` | Skip LLM and Linear calls and produce debug output |
| `include_appendix` | `false` | Add diff stats, commit list and per-commit details |
| `output_path` | `release_notes/v{version}.md` | Output Markdown path |
| `metadata_file` | `metadata.json` | JSON metadata output path |

Model settings resolve from the action input, then `.github/giskard/release-notes.json`, then the built-in default. The CLI also reads `RELEASE_NOTES_MODEL`, `RELEASE_NOTES_EFFORT` and the per-stage `RELEASE_NOTES_*` variables between its flags and the config file.

## Outputs

| Output | Description |
| --- | --- |
| `notes_path` | Path of the generated Markdown file |
| `summary` | Summary bullets (multi-line) |
| `compare_url` | GitHub compare URL for the tag range |

## Submodules and vendored manifests

`git show <tag>:<path>` cannot read files inside a submodule (a gitlink). The tool detects gitlinks and reads the nested file from the submodule checkout at the recorded commit, so submodules listed in `manifest_paths` must be checked out **before** the action runs. If objects are missing, the tool logs a warning and skips that manifest instead of reporting an empty bump list.

Release notes stays a composite action for this reason: callers often mint their own GitHub App token to check out private submodules in the same job.

## Configuration

Settings are read from `.github/giskard/release-notes.json` in the analyzed repository. See [Configuration](configuration.html#release-notes) for the fields and schema. If only the v0.3.x file `.release-notes.json` exists, the run fails with an error naming the new path; see [Migrating from v0.3.x](index.html#migrating-from-v03x).

## Local CLI

```bash
uv sync --group dev
uv run generate-release-notes --version 1.2.0 --repo /path/to/your-repo \
  --output output/v1.2.0.md --dry-run
```

Drop `--dry-run` and pass `--env-file .env` (see `.env.example`) for a real run.
