---
title: "0003: Shared CI and required zizmor workflows"
parent: Architecture decisions
nav_order: 3
---

# Shared CI and Required zizmor Workflows in giskard-actions

**Status**: accepted

The reusable lint, format and test workflows (**Shared CI Workflows** `python-ci.yml` and `ts-ci.yml`) and the organization-required zizmor workflow (**Required Scan Workflow** `zizmor-scan.yml`) ship from `giskard-actions` under its **Umbrella Release**, next to the release notes action and the **Reusable Review Workflow**.

## Context

The org-wide repository baseline asks every code repository for a CI that lints, checks formatting and runs tests, and for a zizmor scan of its GitHub Actions files on every pull request. Most repositories are uv Python or npm TypeScript projects, so the same jobs would otherwise be copied, pinned and kept up to date in each of them.

An organization ruleset can require a workflow to pass ("Require workflows to pass before merging") without a caller file in the target repository. The ruleset references the workflow by repository, path and ref, and a public repository can only require a workflow stored in a public repository. A required workflow checks out the pull request's merge commit, so anything it reads from the repository is controlled by the pull request unless it reads it from the base commit. Renovate only proposes SHA pin updates (`helpers:pinGitHubActionDigests`) for repositories that publish SemVer tags.

## Decision

- **The shared workflows live in `giskard-actions`.** It is public, so the required workflow can run on public repositories, and it publishes SemVer tags, so Renovate bumps the callers' pins. A separate repository would need its own release pipeline to get the same.
- **The baseline requires a CI, not these workflows.** A repository satisfies it with any CI that lints, checks formatting and tests; the Shared CI Workflows are the default way to do it. Every command is an input, passed through an environment variable to `bash -c` so no caller value is interpolated into a script.
- **Dependency caching is opt-in.** `enable-cache` (Python) defaults to `false` and `cache` (TypeScript) to empty: callers may run the workflows on release, tag or `workflow_run` events, where a poisoned cache restored from the default branch would run with release credentials, and prebuilt wheels and tarballs re-download quickly.
- **Callers pin by SHA**, like every other reference to this repository (`@<full-commit-sha> # vX.Y.Z`).
- **The ruleset pin is bumped by an organization admin.** The ruleset stores a fixed commit SHA in the organization settings, outside any repository file, so Renovate cannot see it.
- **Only the ruleset rule is the gate.** "Require workflows to pass before merging" pins the repository, path and SHA of the workflow that must pass. A required status check matched by the name `zizmor` is not used: a pull request can satisfy it with a job of the same name in a workflow it adds or edits.
- **The scan reads its config from the base commit.** On pull requests and merge groups, the zizmor config comes from the base commit, so a pull request cannot disable rules to pass its own check. Inline `# zizmor: ignore[...]` comments in the pull request still apply; repositories that need more require code owner review on `.github/**`.
- **The required workflow uses only `actions/checkout` and `zizmorcore/zizmor-action`.** It runs the default persona with `advanced-security: false` (private repositories have no GitHub Advanced Security) and passes when the repository has no workflow, action or Dependabot files.
- **The future organization actions allowlist must include `actions/checkout`, `zizmorcore/zizmor-action` and `github/codeql-action/upload-sarif`.** The runner resolves every action of a job, including those nested in composite actions, when the job starts and before any `if:` is evaluated. `zizmor-action` references `upload-sarif` in a step that only runs with `advanced-security: true`, so a disallowed `upload-sarif` would still fail the job.

## Consequences

- A `feat:` change to any shared workflow cuts a minor release that Renovate proposes to every caller; changes to `zizmor-scan.yml` also need an admin to move the ruleset SHA.
- The check shows as `zizmor` in the ruleset run and as `<caller job> / zizmor` when called as a reusable workflow. Keep the job name stable so callers' checks keep their names.
- `zizmor-action` downloads the zizmor wheel from PyPI on every run (hash-verified against the action's lock), so every pull request in the organization depends on PyPI being reachable.
- This repository runs `zizmor-scan.yml` on its own pull requests next to its pedantic `zizmor.yml`, which exercises the shared workflow before each release.
- Adding an action to `zizmor-scan.yml`, or a `zizmor-action` release that references a new one, requires adding it to the organization allowlist first, otherwise every code repository's pull requests fail.
