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

An organization ruleset can require a workflow to pass ("Require workflows to pass before merging") without a caller file in the target repository. The ruleset references the workflow by repository, path and ref, and a public repository can only require a workflow stored in a public repository. Renovate only proposes SHA pin updates (`helpers:pinGitHubActionDigests`) for repositories that publish SemVer tags.

## Decision

- **The shared workflows live in `giskard-actions`.** It is public, so the required workflow can run on public repositories, and it publishes SemVer tags, so Renovate bumps the callers' pins. A separate repository would need its own release pipeline to get the same.
- **The baseline requires a CI, not these workflows.** A repository satisfies it with any CI that lints, checks formatting and tests; the Shared CI Workflows are the default way to do it. Every command is an input, passed through an environment variable to `bash -c` so no caller value is interpolated into a script.
- **Callers pin by SHA**, like every other reference to this repository (`@<full-commit-sha> # vX.Y.Z`).
- **The ruleset pin is bumped by an organization admin.** The ruleset stores a fixed commit SHA in the organization settings, outside any repository file, so Renovate cannot see it.
- **The required workflow uses only `actions/checkout` and `zizmorcore/zizmor-action`.** It runs in every code repository, so a future organization action allowlist must include both actions at the pinned SHAs. It runs the default persona with `advanced-security: false` (private repositories have no GitHub Advanced Security), honors each repository's `.github/zizmor.yml`, and skips zizmor when the repository has no workflow, action or Dependabot files.

## Consequences

- A `feat:` change to any shared workflow cuts a minor release that Renovate proposes to every caller; changes to `zizmor-scan.yml` also need an admin to move the ruleset SHA.
- The check is named after the job, `zizmor`. Required status check rules and caller jobs match that name, so renaming the job is a breaking change.
- This repository runs `zizmor-scan.yml` on its own pull requests next to its pedantic `zizmor.yml`, which exercises the shared workflow before each release.
- Adding an action to `zizmor-scan.yml` requires adding it to the organization allowlist first, otherwise every code repository's pull requests fail.
