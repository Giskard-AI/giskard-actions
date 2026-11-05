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

## Prerequisites

- **Python CI**: a uv project with a committed `uv.lock`, `ruff` and `pytest` in a dependency group, and at least one test. Otherwise override `lint-command` and `test-command`.
- **TypeScript CI**: a `package.json` with a `lint` script, `prettier` in `devDependencies`, a `test` script, and a committed `package-lock.json` (npm) or `yarn.lock` (Yarn 1). pnpm is not supported yet.
- **Not supported**: pnpm, service containers (for example Postgres for integration tests) and build matrices. Repositories that need them keep their own CI for now.
- **zizmor scan**: nothing in the repository; it runs on GitHub-hosted `ubuntu-latest`, which has Docker.
- **Every caller** grants `contents: read` and passes no secrets.

## Pinning

Pin the full commit SHA of a [release](https://github.com/Giskard-AI/giskard-actions/releases) with its version as a comment, and use the same SHA for every reference to this repository. Get the SHA from the release page or with:

```bash
gh api repos/Giskard-AI/giskard-actions/git/ref/tags/v1.1.0 --jq .object.sha
```

```yaml
uses: Giskard-AI/giskard-actions/.github/workflows/python-ci.yml@<full-commit-sha> # v1.1.0
```

Renovate with `helpers:pinGitHubActionDigests` then proposes updates. See [Pinning and versioning](index.html#pinning-and-versioning).

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
    uses: Giskard-AI/giskard-actions/.github/workflows/python-ci.yml@<full-commit-sha> # v1.1.0
    with:
      working-directory: backend # optional, defaults to the repository root
```
{% endraw %}

The checks appear as `python / lint` and `python / test` (calling job name, then the called job name).

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `python-version` | string | `''` | Python version for uv; empty lets uv choose from `.python-version` or `requires-python` |
| `uv-version` | string | `''` | uv version; empty uses `[tool.uv] required-version` from `uv.toml` or `pyproject.toml` in `working-directory`, else the latest uv |
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
    uses: Giskard-AI/giskard-actions/.github/workflows/ts-ci.yml@<full-commit-sha> # v1.1.0
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

`zizmor-scan.yml` runs [zizmor](https://docs.zizmor.sh/) 1.32.0 from its container image, pinned by digest (`ghcr.io/zizmorcore/zizmor:<version>@sha256:...`), with the default persona. Findings appear as annotations on the pull request and fail the `zizmor` job, which times out after 10 minutes.

- **PR content cannot touch the scanner.** The pull request is checked out into `src/` and mounted read-only into the container, which runs without network. The config is checked out into a sibling directory, never inside `src/`.
- **Config from the base commit.** On `pull_request` and `merge_group` runs, the zizmor config is read from the base commit (`github.event.pull_request.base.sha` or `github.event.merge_group.base_sha`), in zizmor's discovery order: `.github/zizmor.yml`, `.github/zizmor.yaml`, `zizmor.yml`, `zizmor.yaml`. With none, an empty config (`rules: {}`) is used. On other events, such as a reusable call from a `push` workflow, the config of the scanned commit is used.
- **No ignores from the pull request.** zizmor runs with `--no-ignores`: inline `# zizmor: ignore[...]` comments and the config's `ignore:` entries are not honoured. See [Exceptions](#exceptions).
- **Every file counts.** `--collect=all` audits workflows, composite actions, Dependabot and pre-commit files even when `.gitignore` lists them, since GitHub runs a force-added workflow regardless. `--strict-collection` fails on a file zizmor cannot parse.
- **Offline.** Online audits (known vulnerable actions, impostor commits, ref confusion) depend on external advisories and could turn every pull request in the organization red without a code change. They run in the weekly repo-baseline scan instead.
- **No files is a pass.** A repository with nothing to audit passes with a notice.
- **Never call it from `pull_request_target`.** That event checks out the base branch, so the scan would pass on code the pull request does not contain.

> **zizmor failed on my PR?**
> Fix the finding; the annotation names the rule and links its documentation. For a justified exception, merge a `.github/zizmor.yml` change in a separate pull request first, then rebase. The config is read from the base commit, so editing it in the same pull request has no effect, and inline `# zizmor: ignore` comments are not honoured.

### Exceptions

`--no-ignores` drops both inline ignore comments and `rules.<id>.ignore` entries, so the only exceptions are rule-wide, in `.github/zizmor.yml` on the default branch:

```yaml
rules:
  dangerous-triggers:
    disable: true # why this repository needs it
```

Rules with options (`rules.<id>.config`, for example the `unpinned-uses` policies) can be tuned there as well. A disabled rule no longer reports anywhere in the repository, so keep the reason next to it and require code owner review on `.github/**` (a `CODEOWNERS` entry plus "Require review from Code Owners").

### Rollout with an organization ruleset

The workflow triggers on `pull_request` and `merge_group`, so an organization ruleset can run it on code repositories without a caller file. The rollout is progressive (rulesets have no Evaluate mode outside GitHub Enterprise):

1. The repo-baseline dashboard reports which repositories would fail the scan, so they can fix findings or merge exceptions first.
2. An organization ruleset with **Require workflows to pass before merging** requires `.github/workflows/zizmor-scan.yml` from `Giskard-AI/giskard-actions` at the full commit SHA of a release, for repositories whose `zizmor-gate` custom property is `true`. Admins set the property repository by repository once each one is green.
3. Once every code repository is green, the ruleset targets all repositories with `kind=code`.

The ruleset and the `zizmor-gate` property are defined in repo-baseline and applied by admins, not in this repository.

Only this rule is a trustworthy gate: it pins the repository, path and SHA of the workflow that must pass. A required status check that matches the name `zizmor` can be satisfied by a job of the same name in a workflow the pull request itself adds or edits. The check shows as `zizmor` in the ruleset run and as `<caller job> / zizmor` when the workflow is called as a reusable workflow.

The ruleset's SHA lives in the organization settings, not in a repository file, so Renovate cannot bump it. An organization admin updates it when a release changes `zizmor-scan.yml`.

### Dependencies

- **Actions**: only `actions/checkout`. If the organization restricts which actions may run, the allowlist must include it. The zizmor image is started with `docker run` in a script, which the actions allowlist does not cover.
- **GHCR**: every run pulls the zizmor image from `ghcr.io`, so every pull request in the organization depends on it being reachable.
- **Updates**: Renovate proposes new zizmor versions with their digest. GHCR publishes no release timestamps, so the 7-day cooldown cannot apply automatically; the update waits for approval on the Dependency Dashboard, given once the same version has been on PyPI for 7 days.

### Call it as a reusable workflow

Repositories that want the scan before the ruleset covers them, or on other events, can call it:

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
    uses: Giskard-AI/giskard-actions/.github/workflows/zizmor-scan.yml@<full-commit-sha> # v1.1.0
```
{% endraw %}

Its `pull_request` and `merge_group` triggers are ignored when called; the caller's triggers apply, and the base commit is still read from the caller's event. Its concurrency group appends `zizmor-scan` to the caller's workflow name, so it does not collide with a workflow-level group on the caller.

This repository's own `zizmor.yml` workflow runs the same image with the pedantic persona and online audits. It keeps line-scoped inline ignores (`--no-config`), while `.github/zizmor.yml` holds the rule-wide exceptions the shared scan needs here.
