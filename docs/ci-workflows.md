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

### zizmor failed on my PR?
{: #zizmor-failed-on-my-pr }

- **Fix the finding.** The annotation names the rule and links its documentation.
- **For a justified exception**, add `# zizmor: ignore[<rule>] -- <reason>` on the flagged line (see [Exceptions](#exceptions)). The scan flags it as a new ignore so your reviewers check the reason.
- **Editing `.github/zizmor.yml` in your pull request has no effect.** The config is read from the base commit. A repository-wide change goes in its own pull request first; then push a commit or click **Update branch** (re-running the job reuses the old base commit).
- **A symlinked action, workflow or `.github` file fails the scan.** Commit the file itself.

### How it runs

`zizmor-scan.yml` runs [zizmor](https://docs.zizmor.sh/) 1.32.0 from its container image, pinned by digest (`ghcr.io/zizmorcore/zizmor:<version>@sha256:...`), with the pedantic persona. Findings appear as annotations on the pull request and fail the `zizmor` job, which times out after 10 minutes.

- **PR content cannot touch the scanner.** The pull request is checked out into `src/` and mounted read-only into the container, which runs without network. The base commit's YAML files are checked out into a sibling `base/` directory, never inside `src/`.
- **Pedantic persona.** The scan reports code-smell findings too, the same standard as a repository's own pedantic zizmor check. A repository cannot choose another persona.
- **No symlinked Actions files.** The runner follows a symlink when it loads a local action, but zizmor audits the target under its own name, or skips it when the target is outside the checkout. The job fails before scanning if `action.yml`, `action.yaml`, a `.github` directory or anything under one is a symlink.
- **Config from the base commit.** On `pull_request` and `merge_group` runs, the zizmor config is read from the base commit (`github.event.pull_request.base.sha` or `github.event.merge_group.base_sha`), in zizmor's discovery order: `.github/zizmor.yml`, `.github/zizmor.yaml`, `zizmor.yml`, `zizmor.yaml`. With none, an empty config (`rules: {}`) is used. On other events, such as a reusable call from a `push` workflow, the config of the scanned commit is used.
- **Inline ignores are honoured and flagged.** `# zizmor: ignore[...]` comments and the config's `ignore:` entries apply. Each ignore comment a pull request adds, compared with the base commit, becomes a warning on its line and is listed in the job summary. The warning never fails the job. See [Exceptions](#exceptions).
- **Every file counts.** `--collect=all` audits workflows, composite actions, Dependabot and pre-commit files even when `.gitignore` lists them, since GitHub runs a force-added workflow regardless. `--strict-collection` fails on a file zizmor cannot parse.
- **Offline.** Online audits (known vulnerable actions, impostor commits, ref confusion) depend on external advisories and could turn every pull request in the organization red without a code change. They run in the weekly repo-baseline scan instead.
- **No files is a pass.** A repository with nothing to audit passes with a notice.
- **Never call it from `pull_request_target`.** That event checks out the base branch, so the scan would pass on code the pull request does not contain.

### Exceptions

An exception is an inline ignore on the flagged line, with a one-line reason, reviewed in the pull request that adds it:

```yaml
on: # zizmor: ignore[dangerous-triggers] -- /review gated to OWNER/MEMBER/COLLABORATOR
  issue_comment:
```

The scan flags every ignore comment the pull request adds, so reviewers see each one. Keep the reason specific enough for a reviewer, or a later auditor, to judge it.

Repository-wide policy (disabling a rule everywhere, or tuning options such as the `unpinned-uses` policies with `rules.<id>.config`) goes in `.github/zizmor.yml`, read from the base commit, so a pull request cannot loosen it for itself. Require code owner review on `.github/**` (a `CODEOWNERS` entry plus "Require review from Code Owners").

Strict collection failures cannot be excepted: a file zizmor cannot parse (test fixtures, Jinja or copier templates, syntax zizmor does not support) fails the scan. The repo-baseline dashboard dry run surfaces them before a repository is gated.

### Rollout with an organization ruleset

The workflow triggers on `pull_request` and `merge_group`, so an organization ruleset can run it on code repositories without a caller file. The rollout is progressive (rulesets have no Evaluate mode outside GitHub Enterprise):

1. The repo-baseline dashboard reports which repositories would fail the scan, and lists their ignore comments, so they can fix findings or add reasoned ignores first.
2. An organization ruleset with **Require workflows to pass before merging** requires `.github/workflows/zizmor-scan.yml` from `Giskard-AI/giskard-actions` at the full commit SHA of a release, for repositories whose `zizmor-gate` custom property is `true`. Admins set the property repository by repository once each one is green.
3. Once every code repository is green, the ruleset targets all repositories with `kind=code`.

The ruleset JSON and the `zizmor-gate` property live in repo-baseline as code (repo-baseline ADR 0001) and change through a reviewed pull request there, where Renovate can propose the workflow SHA bump; admins apply them.

Only this rule is a trustworthy gate: it pins the repository, path and SHA of the workflow that must pass. A required status check that matches the name `zizmor` can be satisfied by a job of the same name in a workflow the pull request itself adds or edits. The check shows as `zizmor` in the ruleset run and as `<caller job> / zizmor` when the workflow is called as a reusable workflow.

### Operations

- **Emergency.** If the scan blocks a repository and cannot be fixed in time, an organization owner unsets that repository's `zizmor-gate` property, or disables the ruleset. Never merge around it with a bypass.
- **Gated repositories drop their direct call.** Once the ruleset gates a repository, remove any `workflow_call` of `zizmor-scan.yml` from it, or the scan runs twice.
- **zizmor upgrades are one monthly admin step.** A ruleset bump reaches every gated repository at once, so: approve the update on the Renovate Dependency Dashboard, cut the release, dry-run the new version in the repo-baseline dashboard, then bump the ruleset SHA in repo-baseline.

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

This repository's own `zizmor.yml` workflow (job `zizmor-pedantic`, so the `zizmor` check name stays unique) runs the same image with the pedantic persona and online audits. The online audits are the only thing it adds to the shared scan.
