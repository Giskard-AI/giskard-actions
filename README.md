# giskard-actions

Reusable GitHub Actions automation from [Giskard](https://github.com/Giskard-AI):

| What | Form | Entry point |
| --- | --- | --- |
| **Release notes**: structured, LLM-written release notes from git history, with optional Linear and GitHub issue enrichment | Composite action | `Giskard-AI/giskard-actions/.github/actions/release-notes` |
| **Codex PR review**: advisory pull request review by parallel [Codex](https://github.com/openai/codex-action) reviewer jobs plus an aggregator, posted as a PR review comment | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/codex-pr-review.yml` |
| **Python CI**: `lint` (ruff format check and lint) and `test` (pytest) jobs for uv projects, every command overridable | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/python-ci.yml` |
| **TypeScript CI**: `lint` (lint and prettier check) and `test` jobs for npm projects, every command overridable | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/ts-ci.yml` |
| **zizmor scan**: [zizmor](https://docs.zizmor.sh/) audit of every GitHub Actions file, offline, auditor persona, from a digest-pinned image; exceptions are inline ignores with a reason, and the ones a pull request adds are flagged for review; the config comes from the pull request's base commit; passes when there are no such files | Reusable workflow, and required workflow for organization rulesets | `Giskard-AI/giskard-actions/.github/workflows/zizmor-scan.yml` |
| **gha-hardening**: agent skill that brings one repository to a passing zizmor Gate in a draft pull request, audits its secrets, rulesets and GitHub Apps, and creates dedicated Apps with a wizard | Agent skill (Cursor, Claude Code) | [`skills/gha-hardening/`](skills/gha-hardening/SKILL.md) |

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
        uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<full-commit-sha> # vX.Y.Z
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
    uses: Giskard-AI/giskard-actions/.github/workflows/codex-pr-review.yml@<full-commit-sha> # vX.Y.Z
    with:
      environment: azure-codex-review # environment holding AZURE_AI_API_KEY and AZURE_AI_RESPONSES_BASE
    secrets:
      AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
```

Your repository keeps the triggers, the comment gate, concurrency and the permission ceiling. The `author_association` gate is a cheap pre-filter; the workflow then confirms the commenter has write, maintain or admin permission before reviewing. Pass the secret explicitly by name; never use `secrets: inherit`. Reviewer selection lives in `.github/giskard/codex-reviewer-map.json`, and reviewer personas in `<agents_dir>/<name>.md` (default `.cursor/agents`), both read from the **default branch** HEAD.

Each reviewer pass runs in its own job with sudo dropped before Codex starts, at most `max_parallel` (default 4) at a time, followed by an aggregate job and a post job. Jobs default to `ubuntu-slim` with a 15-minute timeout each, matching GitHub's hard cap for that runner. Self-hosted runners need `allow_self_hosted: true`. See the [Codex PR review docs](https://giskard-ai.github.io/giskard-actions/codex-pr-review.html) for every input and the security model.

## Quick start: shared CI workflows

Get the commit SHA of a release from the [releases page](https://github.com/Giskard-AI/giskard-actions/releases) or with `gh api repos/Giskard-AI/giskard-actions/git/ref/tags/v1.1.0 --jq .object.sha`, and pin it with the version as a comment. A Python project:

```yaml
name: CI

on:
  pull_request:
  merge_group:
  push:
    branches: [main]

permissions: {}

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  python:
    permissions:
      contents: read
    uses: Giskard-AI/giskard-actions/.github/workflows/python-ci.yml@<full-commit-sha> # v1.1.0
```

A TypeScript project uses the same file with this job instead (add both jobs only when the repository has both):

```yaml
jobs:
  frontend:
    permissions:
      contents: read
    uses: Giskard-AI/giskard-actions/.github/workflows/ts-ci.yml@<full-commit-sha> # v1.1.0
    with:
      working-directory: frontend # optional, defaults to the repository root
```

Every command is an input (`lint-command`, `test-command`, ...). Never add `secrets: inherit`; the workflows take no secret. `zizmor-scan.yml` is required on every code repository by an organization ruleset ("Require workflows to pass before merging", pinned to this repository, path and SHA); it can also be called the same way. See the [shared CI workflows docs](https://giskard-ai.github.io/giskard-actions/ci-workflows.html) for prerequisites, inputs and the zizmor exception workflow.

## Agent skill: gha-hardening

Install it from a release, so its scripts are the reviewed ones:

```bash
git clone --depth 1 --branch vX.Y.Z https://github.com/Giskard-AI/giskard-actions /tmp/giskard-actions
cp -r /tmp/giskard-actions/skills/gha-hardening ~/.cursor/skills/   # or ~/.claude/skills/
```

Then ask the agent: `/gha-hardening Giskard-AI/<repo>` from a clone of the repository. It needs `gh` (logged in), `jq`, and Docker or `uv`.

## Pinning and versioning

- The whole repository shares **one SemVer version**. Tags `vX.Y.Z` and GitHub Releases are cut only by [`release.yml`](.github/workflows/release.yml) on pushes to `main`, from conventional commits since the last tag: `fix`, `perf` or any type with the `deps` scope (for example `chore(deps): ...` from Renovate, including its weekly lock file maintenance) is a patch, `feat` a minor, and a `!` header or a `BREAKING CHANGE:` footer a major (a minor while the version is `0.x`). Merged dependency updates therefore always publish a patch release. A range with none of these (only `chore`, `docs`, `test`, `ci`, ... without the `deps` scope) is not released. Maintainers can run `release.yml` manually on `main` with a `version` input to release an explicit `X.Y.Z` instead (for example the first `1.0.0`); it must be higher than every existing tag.
- There are no floating (`v0`) or per-action tags. Pin the **full 40-character commit SHA** of a [release](https://github.com/Giskard-AI/giskard-actions/releases) with a version comment: `@<sha> # vX.Y.Z`. Use the same SHA for every reference to this repository.
- The version lives only in the git tags. `version` in `pyproject.toml` stays `0.0.0` and is never bumped.
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
  "$schema": "https://raw.githubusercontent.com/Giskard-AI/giskard-actions/v1.0.0/schemas/release-notes.schema.json"
}
```

## Security model

- **Trusted code is the pinned unpack.** Helper scripts, prompts, output schemas and the model catalog run from the action directory GitHub unpacks at your pinned SHA (`github.action_path`). Callers cannot point the helpers at other code, and the Codex helper never runs `scripts/` from your workspace.
- **Reviewer configuration comes from the default branch.** The reviewer map, personas and role references are checked out from the default branch HEAD, never from the PR head or its base commit, so a pull request cannot rewrite or retarget its own review instructions.
- **Least privilege.** Workflows declare `permissions: {}` and grant per job. Only the prepare job reads GitHub; only reviewer and aggregate jobs receive the Azure key, and only `openai/codex-action` and the Key Leak Check use it; only the separate post job can write a PR review.
- **Codex cannot reach the key.** Each Codex pass runs in its own job with sudo dropped before Codex starts (`drop-sudo`), in a read-only bubblewrap sandbox without web search. Every output is checked for the key before it is uploaded. Anyone with write access can still read CI secrets through a workflow change; see the [security model](https://giskard-ai.github.io/giskard-actions/codex-pr-review.html#security-model).
- **Untrusted input stays data.** Diffs, comments and logs are treated as untrusted evidence in prompts. Only comments by owners, members and collaborators reach Codex, and release notes only enrich issues and pull requests opened by them or by accounts the repository lists by user id in `trusted_github_authors`. Model output (review bodies and release notes) is sanitized before publishing: secret patterns redacted, images, raw HTML and invisible characters dropped, and links restricted to GitHub and SonarCloud (release notes: your repository on GitHub only). Only same-repository pull requests are reviewed.

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
