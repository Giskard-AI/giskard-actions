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

Runs `uv sync` then the lint command in a `lint` job, and `uv sync` then the test command in a parallel `test` job.

{% raw %}
```yaml
name: CI

on:
  pull_request:
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
| `python-version` | string | `3.12` | Python version installed by `astral-sh/setup-uv` |
| `uv-version` | string | `0.13.6` | uv version |
| `working-directory` | string | `.` | Directory holding `pyproject.toml` and `uv.lock`; every command runs there |
| `runner` | string | `ubuntu-latest` | Runner label for both jobs |
| `sync-args` | string | `--locked --all-groups` | Arguments of `uv sync`, split on whitespace |
| `lint-command` | string | `uv run ruff format --check . && uv run ruff check .` | Command of the `lint` job |
| `test-command` | string | `uv run pytest` | Command of the `test` job |

`--locked` fails when `uv.lock` is out of date with `pyproject.toml`; `--all-groups` installs every dependency group, so the default commands need `ruff` and `pytest` in one of them.

## TypeScript CI

Runs the install, lint and format commands in a `lint` job, and the install and test commands in a parallel `test` job. `actions/setup-node` caches the npm cache keyed on `<working-directory>/package-lock.json`, so the project must commit that file.

{% raw %}
```yaml
name: CI

on:
  pull_request:
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
| `working-directory` | string | `.` | Directory holding `package.json` and `package-lock.json`; every command runs there |
| `runner` | string | `ubuntu-latest` | Runner label for both jobs |
| `install-command` | string | `npm ci` | Install command, run in both jobs |
| `lint-command` | string | `npm run lint` | Lint step of the `lint` job |
| `format-command` | string | `npx prettier --check .` | Format check step of the `lint` job |
| `test-command` | string | `npm test` | Command of the `test` job |

## Command inputs

Command inputs run as `bash -eo pipefail -c "<command>"` from an environment variable, never as an expression inside the script, so zizmor reports no template injection. They are still code: set them to fixed strings in your workflow and never build them from pull request data such as branch names or titles.

## Caller permissions

Both CI workflows and the zizmor scan only need `contents: read`. Grant it on the calling job, which caps every job of the called workflow. Put concurrency on the caller as well; the CI workflows do not declare it.

## zizmor scan

`zizmor-scan.yml` runs [zizmor](https://docs.zizmor.sh/) with its default persona (not pedantic) and `advanced-security: false`, because private repositories have no GitHub Advanced Security. Findings fail the `zizmor` job and appear in the job log.

- It checks out the repository the run belongs to, so zizmor discovers that repository's own `.github/zizmor.yml` (or `zizmor.yml`) and applies its rules and ignores.
- A first step lists tracked workflow, composite action and Dependabot files. When there are none, zizmor is skipped and the job passes, so the scan can be required on repositories without workflows.
- The job is named `zizmor`, which is the check name in pull requests. It stays stable so required status check rules can match it.

This repository's own `zizmor.yml` workflow keeps the pedantic persona.

### Enforce it with an organization ruleset

The workflow triggers on `pull_request` and `merge_group`, so an organization ruleset can run it on every code repository without a caller file:

1. In the organization settings, create a branch ruleset that targets the code repositories (for example by the `kind=code` custom property) and their default branch.
2. Enable **Require workflows to pass before merging** and add `.github/workflows/zizmor-scan.yml` from `Giskard-AI/giskard-actions`, at the full commit SHA of a release.
3. Start in **Evaluate** mode. Rule insights then show which pull requests would have been blocked without blocking them. Fix or configure the findings in those repositories (`.github/zizmor.yml`), then switch the ruleset to **Active**.

The ruleset references a fixed SHA that lives in the organization settings, not in a repository file, so Renovate cannot bump it. An organization admin updates the SHA when a release changes `zizmor-scan.yml`.

### Call it as a reusable workflow

Repositories that want the scan before the ruleset is active, or on other events, can call it:

{% raw %}
```yaml
name: zizmor

on:
  pull_request:
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

Its `pull_request` and `merge_group` triggers are ignored when called; the caller's triggers apply. Its concurrency group appends `zizmor-scan` to the caller's workflow name, so it does not collide with a workflow-level group on the caller.
