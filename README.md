# giskard-actions

Reusable GitHub Actions automation from [Giskard](https://github.com/Giskard-AI):

| What | Form | Entry point |
| --- | --- | --- |
| **Release notes**: structured, LLM-written release notes from git history, with optional Linear and GitHub issue enrichment | Composite action | `Giskard-AI/giskard-actions/.github/actions/release-notes` |
| **Codex PR review**: advisory pull request review by sequential [Codex](https://github.com/openai/codex-action) reviewer passes plus an aggregator, posted as a PR review comment | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/codex-pr-review.yml` |

Full documentation: <https://giskard-ai.github.io/giskard-actions/>

## Quick start: release notes

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

      - id: notes
        uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<full-commit-sha> # v0.4.0
        with:
          version: "1.2.0" # tag v1.2.0 must exist (or use dry_run)
        env:
          AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
          AZURE_AI_API_BASE: ${{ vars.AZURE_AI_API_BASE }}
          AZURE_AI_ENDPOINT: ${{ vars.AZURE_AI_API_BASE }}
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }} # optional; private repositories only
          GITHUB_TOKEN: ${{ github.token }}

      - run: cat "${NOTES_PATH}"
        env:
          NOTES_PATH: ${{ steps.notes.outputs.notes_path }}
```

Credentials go on the step `env:`. Linear enrichment copies ticket text into the model's context and can surface it in the notes, so do not set `LINEAR_API_KEY` in a public repository (see [Release notes docs](https://giskard-ai.github.io/giskard-actions/release-notes.html#linear-enrichment)). Configure sections, manifests and upgrade rules in `.github/giskard/release-notes.json` (see [Configuration](#configuration)). If `manifest_paths` point into git submodules, check them out first (`submodules: true` or your own GitHub App token): that is why release notes stays a composite action running inside your job.

## Quick start: Codex PR review

```yaml
name: Codex PR review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled]
  issue_comment:
    types: [created]

permissions: {}

jobs:
  codex:
    # Comment gate: only trusted collaborators can start a review.
    if: >-
      (
        github.event_name == 'pull_request' &&
        contains(github.event.pull_request.labels.*.name, 'codex-review')
      ) ||
      (
        github.event_name == 'issue_comment' &&
        github.event.issue.pull_request &&
        contains(fromJSON('["/review","/codex-assess"]'), github.event.comment.body) &&
        contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)
      )
    # Job-level, so comments that fail the gate never queue into (and cancel) the group.
    concurrency:
      group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.event.issue.number }}
      cancel-in-progress: ${{ github.event_name != 'issue_comment' }}
    # Permission ceiling for every job in the called workflow.
    permissions:
      contents: read
      issues: read
      pull-requests: write
    uses: Giskard-AI/giskard-actions/.github/workflows/codex-pr-review.yml@<full-commit-sha> # v0.4.0
    with:
      environment: ci # environment holding AZURE_AI_API_KEY and AZURE_AI_RESPONSES_BASE
    secrets:
      AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
```

Your repository keeps the triggers, the comment gate, concurrency and the permission ceiling. The `author_association` gate is a cheap pre-filter; the workflow then confirms the commenter has write, maintain or admin permission before reviewing. Pass the secret explicitly by name; never use `secrets: inherit`. Reviewer selection lives in `.github/giskard/codex-reviewer-map.json`, and reviewer personas in `.cursor/agents/<name>.md`, both read from the pull request **base** commit.

The review job defaults to `ubuntu-slim` with a 15-minute timeout, matching GitHub's hard cap for that runner; typical reviews take a few minutes. For large pull requests, pass `runner: ubuntu-latest` and a larger `timeout_minutes`. See the [Codex PR review docs](https://giskard-ai.github.io/giskard-actions/codex-pr-review.html) for every input.

## Pinning and versioning

- The whole repository shares **one SemVer version**. Tags `vX.Y.Z` and GitHub Releases are cut only by [`release.yml`](.github/workflows/release.yml) on pushes to `main`, from conventional commits since the last tag: `fix`, `perf` or any type with the `deps` scope (for example `chore(deps): ...` from Renovate or the lockfile workflows) is a patch, `feat` a minor, and a `!` header or a `BREAKING CHANGE:` footer a major (a minor while the version is `0.x`). Merged dependency updates therefore always publish a patch release. A range with none of these (only `chore`, `docs`, `test`, `ci`, ... without the `deps` scope) is not released.
- There are no floating (`v0`) or per-action tags. Pin the **full 40-character commit SHA** with a version comment: `@<sha> # v0.4.0`. Use the same SHA for every reference to this repository.
- [Renovate](https://docs.renovatebot.com/) with `helpers:pinGitHubActionDigests` keeps the SHA and comment up to date.
- The reusable workflow calls its own composites with the self-repository syntax `$/`, so they always run from the commit you pinned. It needs GitHub Actions runner 2.336.0 or later; GitHub-hosted runners already qualify.

## Configuration

Consumer configuration lives in `.github/giskard/` of **your** repository. Each file has a published JSON Schema; add a `$schema` key for editor completion.

| File | Used by | Schema | Example |
| --- | --- | --- | --- |
| `.github/giskard/release-notes.json` | Release notes | [`schemas/release-notes.schema.json`](schemas/release-notes.schema.json) | [`examples/web-app.release-notes.json`](examples/web-app.release-notes.json), [`python-library`](examples/python-library.release-notes.json), [`minimal`](examples/minimal.release-notes.json) |
| `.github/giskard/codex-reviewer-map.json` | Codex PR review | [`schemas/codex-reviewer-map.schema.json`](schemas/codex-reviewer-map.schema.json) | [`examples/codex-reviewer-map.json`](examples/codex-reviewer-map.json) |
| `.github/giskard/codex-ci-config.json` | Codex CI triage helper (**experimental, internal**: no shipped action reads it yet) | [`schemas/codex-ci-config.schema.json`](schemas/codex-ci-config.schema.json) | [`examples/codex-ci-config.json`](examples/codex-ci-config.json) |

The schemas' `$id` points at `main`. Reference the schema of the release you pin instead, so editor validation matches the code you run:

```json
{
  "$schema": "https://raw.githubusercontent.com/Giskard-AI/giskard-actions/v0.4.0/schemas/release-notes.schema.json"
}
```

## Migrating from v0.3.x

v0.4.0 moves every action under `.github/actions/` and every consumer file under `.github/giskard/`. If only a legacy config file (`.release-notes.json`, `.github/codex/reviewer-map.json`) exists, the run still reads it and emits a warning naming the new path; the legacy paths stop being read in v1.0.0. When both files exist, the new one wins and the run warns that the legacy one is ignored. Because the Codex review reads its config from the PR base, the PR that moves the file is reviewed with the legacy file and passes.

| v0.3.x | v0.4.0 |
| --- | --- |
| `uses: Giskard-AI/giskard-actions@<sha>` | `uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<sha>` |
| `.release-notes.json` | `.github/giskard/release-notes.json` |
| `.github/codex/reviewer-map.json` | `.github/giskard/codex-reviewer-map.json` |
| Two jobs calling `.github/actions/codex-pr-review` and `.github/actions/codex-post-review` | One job calling `.github/workflows/codex-pr-review.yml` ([Quick start](#quick-start-codex-pr-review)) |

```bash
git mv .release-notes.json .github/giskard/release-notes.json
git mv .github/codex/reviewer-map.json .github/giskard/codex-reviewer-map.json
```

## Security model

- **Trusted code is the pinned unpack.** Helper scripts, prompts, output schemas and the model catalog run from the action directory GitHub unpacks at your pinned SHA (`github.action_path`). Callers cannot point the helpers at other code, and the Codex helper never runs `scripts/` from your workspace.
- **Reviewer configuration comes from the PR base SHA.** The reviewer map, personas and role references are checked out from the pull request base commit, never from the PR head, so a pull request cannot rewrite its own review instructions.
- **Least privilege.** Workflows declare `permissions: {}` and grant per job. The review job only reads; only the separate post job can write a PR review. The Azure key is passed only to `openai/codex-action`, which runs Codex in a read-only sandbox (bubblewrap; `codex sandbox` is probed before the first pass).
- **Untrusted input stays data.** Diffs, comments and logs are treated as untrusted evidence in prompts. Only comments by owners, members and collaborators reach Codex, and release notes only enrich issues and pull requests opened by them. Model output (review bodies and release notes) is sanitized before publishing: secret patterns redacted, images, raw HTML and invisible characters dropped, and links restricted to GitHub and SonarCloud (release notes: your repository on GitHub only). Only same-repository pull requests are reviewed.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Local development

Requires [uv](https://docs.astral.sh/uv/).

```bash
make setup          # uv sync --group dev
make test           # uv run pytest
make format         # ruff format + ruff check --fix
make check_linting  # ruff format --check + ruff check
make lock-upgrade   # uv lock -U
```

Run the CLI locally:

```bash
uv run generate-release-notes --version 1.2.0 --repo /path/to/your-repo \
  --output output/v1.2.0.md --dry-run
```

Drop `--dry-run` and set the variables from [`.env.example`](.env.example) for a real run.

## License

[Apache License 2.0](LICENSE)
