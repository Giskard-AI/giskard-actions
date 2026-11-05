---
title: Shared CI workflows
nav_order: 4
---

# Shared CI workflows
{: .no_toc }

Three reusable workflows for the CI every Giskard code repository needs: lint, format check and tests for uv Python projects (`python-ci.yml`) and Node/TypeScript projects (`ts-ci.yml`), and a zizmor scan of GitHub Actions files (`zizmor-scan.yml`) that an organization ruleset can require on every pull request.

The repository baseline requires a CI that lints, checks formatting and runs tests, not these workflows. Use them when their defaults fit; keep your own CI when they do not.

1. TOC
{:toc}

## Pinning

Pin the full commit SHA of a [release](https://github.com/Giskard-AI/giskard-actions/releases) with a version comment, like every other reference to this repository. Renovate with `helpers:pinGitHubActionDigests` proposes updates. See [Pinning and versioning](index.html#pinning-and-versioning).

## Python CI

Runs `uv sync` then the lint command in a `lint` job, and `uv sync` then the test command in a parallel `test` job. Both jobs set `UV_NO_SYNC=1`, so `uv run` uses the environment `uv sync` built and does not add back groups that `sync-args` left out.

{% raw %}
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
    uses: Giskard-AI/giskard-actions/.github/workflows/python-ci.yml@<full-commit-sha> # vX.Y.Z
    with:
      working-directory: backend # optional, defaults to the repository root
```
{% endraw %}

The checks appear as `python / lint` and `python / test` (calling job name, then the called job name).

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `python-version` | string | `''` | Python version for uv; empty lets uv choose from `.python-version` or `requires-python` |
| `uv-version` | string | `0.13.6` | uv version. setup-uv installs it instead of reading `[tool.uv] required-version`, and uv still fails if it does not satisfy that constraint |
| `working-directory` | string | `.` | Directory holding `pyproject.toml` and `uv.lock`; every command runs there |
| `runner` | string | `ubuntu-latest` | Runner label for both jobs |
| `sync-args` | string | `--locked --all-groups` | Arguments of `uv sync`, split on whitespace |
| `lint-command` | string | `uv run ruff format --check . && uv run ruff check .` | Command of the `lint` job |
| `test-command` | string | `uv run pytest` | Command of the `test` job |
| `enable-cache` | boolean | `false` | Opt in to setup-uv's GitHub Actions cache of uv downloads; see [Caching](#caching) |
| `timeout-minutes` | number | `30` | Timeout of each job |

`--locked` fails when `uv.lock` is out of date with `pyproject.toml`; `--all-groups` installs every dependency group, so the default commands need `ruff` and `pytest` in one of them.

## TypeScript CI

Runs the install, lint and format commands in a `lint` job, and the install and test commands in a parallel `test` job.

{% raw %}
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
  frontend:
    permissions:
      contents: read
    uses: Giskard-AI/giskard-actions/.github/workflows/ts-ci.yml@<full-commit-sha> # vX.Y.Z
    with:
      working-directory: frontend # optional, defaults to the repository root
```
{% endraw %}

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `node-version` | string | `26` | Node.js version spec; 26 is the Active LTS line since 2026-10-28 |
| `working-directory` | string | `.` | Directory holding `package.json`; every command runs there |
| `runner` | string | `ubuntu-latest` | Runner label for both jobs |
| `cache` | string | `''` | Set to `npm` or `yarn` to opt in to `actions/setup-node`'s dependency cache; empty is off. See [Caching](#caching) |
| `cache-dependency-path` | string | `''` | Lockfile(s) keying the cache when `cache` is set, relative to the repository root; empty means `<working-directory>/package-lock.json` |
| `install-command` | string | `npm ci` | Install command, run in both jobs |
| `lint-command` | string | `npm run lint` | Lint step of the `lint` job |
| `format-command` | string | `npx --no prettier --check .` | Format check step of the `lint` job; `--no` fails when prettier is not installed instead of downloading it |
| `test-command` | string | `npm test` | Command of the `test` job |
| `timeout-minutes` | number | `30` | Timeout of each job |

Package managers:

- **npm workspaces**: when caching, point the cache at the root lockfile with `cache-dependency-path: package-lock.json` if `working-directory` is a workspace package.
- **Yarn 1**: set the `yarn ...` commands, and to cache, `cache: yarn` with `cache-dependency-path: <dir>/yarn.lock`. The runner image's Yarn 1.22 is used.
- **pnpm is not supported yet.** The pnpm binary would have to be installed inside the called job (a caller cannot add `pnpm/action-setup` to it), and Node.js 25 and later no longer ship Corepack.

## Caching

Dependency caching is off by default in both CI workflows. GitHub Actions caches written on the default branch are restored by every other ref, including tag pushes and release runs, so anything that can write that cache (a compromised dependency or build step, a workflow that runs untrusted code) can poison what a later release or secret-holding run restores. Callers may run these workflows on release, tag or `workflow_run` events. Prebuilt wheels and npm tarballs re-download quickly, so leaving the cache off costs little.

Opt in with `enable-cache: true` (Python CI) or `cache: npm` / `cache: yarn` (TypeScript CI), only in workflows that run on pull requests and pushes and never in a workflow that publishes, releases or receives secrets.

## Command inputs

Command inputs run as `bash -eo pipefail -c "<command>"` from an environment variable, never as an expression inside the script, so zizmor reports no template injection. They are still code: set them to fixed strings in your workflow and never build them from pull request data such as branch names or titles.

The defaults fail on purpose when the project has no tests (pytest exits with code 5), no `lint` script, or no ruff, pytest or prettier installed; set `lint-command`, `test-command` or `format-command` to what the project actually runs.

## Caller permissions and secrets

Both CI workflows and the zizmor scan only need `contents: read`. Grant it on the calling job, which caps every job of the called workflow. Put concurrency on the caller as well; the CI workflows do not declare it. None of them takes a secret: never add `secrets: inherit`, which would hand every repository secret to the commands.

## zizmor scan

`zizmor-scan.yml` runs [zizmor](https://docs.zizmor.sh/) with its default persona (not pedantic), `advanced-security: false` (private repositories have no GitHub Advanced Security) and `annotations: true`, so findings appear as annotations on the pull request and fail the `zizmor` job. The job times out after 10 minutes.

- **Config from the base commit.** On `pull_request` and `merge_group` runs, the zizmor config is read from the base commit (`github.event.pull_request.base.sha` or `github.event.merge_group.base_sha`), in zizmor's discovery order: `.github/zizmor.yml`, `.github/zizmor.yaml`, `zizmor.yml`, `zizmor.yaml`. With none, an empty config (`rules: {}`) is used. A pull request therefore cannot disable rules in its own config to turn the check green. On other events, such as a reusable call from a `push` workflow, zizmor discovers the config of the checked-out commit.
- **Inline ignores are not covered.** A pull request can still add `# zizmor: ignore[<rule>]` comments to its workflows. Where that matters, require code owner review on `.github/**` (a `CODEOWNERS` entry plus the "Require review from Code Owners" rule).
- **No workflow files is a pass.** `fail-on-no-inputs: false` lets the job succeed with a warning when the repository has no workflow, action or Dependabot files.
- **PyPI at runtime.** `zizmorcore/zizmor-action` downloads the zizmor wheel from PyPI on every run and verifies it against hashes pinned in the action, so every pull request in the organization depends on PyPI being reachable.

This repository's own `zizmor.yml` workflow keeps the pedantic persona.

### Enforce it with an organization ruleset

The workflow triggers on `pull_request` and `merge_group`, so an organization ruleset can run it on every code repository without a caller file:

1. In the organization settings, create a branch ruleset that targets the code repositories (for example by the `kind=code` custom property) and their default branch.
2. Enable **Require workflows to pass before merging** and add `.github/workflows/zizmor-scan.yml` from `Giskard-AI/giskard-actions`, at the full commit SHA of a release.
3. Start in **Evaluate** mode. Rule insights then show which pull requests would have been blocked without blocking them. Fix or configure the findings in those repositories (`.github/zizmor.yml`), then switch the ruleset to **Active**.

Only this rule is a trustworthy gate: it pins the repository, path and SHA of the workflow that must pass. A required status check that matches the name `zizmor` can be satisfied by a job of the same name in a workflow the pull request itself adds or edits. The check shows as `zizmor` in the ruleset run and as `<caller job> / zizmor` when the workflow is called as a reusable workflow.

The ruleset references a fixed SHA that lives in the organization settings, not in a repository file, so Renovate cannot bump it. An organization admin updates the SHA when a release changes `zizmor-scan.yml`.

### Organization actions allowlist

If the organization restricts which actions may run, the allowlist must include:

- `actions/checkout`
- `zizmorcore/zizmor-action`
- `github/codeql-action/upload-sarif`, which `zizmor-action` references in a step that only runs with `advanced-security: true`. The runner resolves every action of a job, including those nested in composite actions, when the job starts and before any `if:` is evaluated, so a disallowed action fails the job even when its step would be skipped.

### Call it as a reusable workflow

Repositories that want the scan before the ruleset is active, or on other events, can call it:

{% raw %}
```yaml
name: zizmor

on:
  pull_request:
  merge_group:
  push:
    branches: [main]

permissions: {}

jobs:
  zizmor:
    permissions:
      contents: read
    uses: Giskard-AI/giskard-actions/.github/workflows/zizmor-scan.yml@<full-commit-sha> # vX.Y.Z
```
{% endraw %}

Its `pull_request` and `merge_group` triggers are ignored when called; the caller's triggers apply, and the base commit is still read from the caller's event. Its concurrency group appends `zizmor-scan` to the caller's workflow name, so it does not collide with a workflow-level group on the caller.
