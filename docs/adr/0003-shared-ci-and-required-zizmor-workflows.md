---
title: "0003: Shared CI and required zizmor workflows"
parent: Architecture decisions
nav_order: 3
---

# Shared CI and Required zizmor Workflows in giskard-actions

**Status**: accepted

The reusable lint, format and test workflows (**Shared CI Workflows** `python-ci.yml` and `ts-ci.yml`) and the organization-required zizmor workflow (**Required Scan Workflow** `zizmor-scan.yml`) ship from `giskard-actions` under its **Umbrella Release**, next to the release notes action and the **Reusable Review Workflow**.

**Summary for managers**

- **Cost**: about one runner minute per pull request for the zizmor scan, on top of the lint and test jobs a repository already runs, and one monthly admin step to upgrade zizmor.
- **Friction**: a pull request that adds a risky workflow pattern is blocked until it is fixed or a rule-wide exception is merged first; repositories are gated one by one once green, but a zizmor upgrade reaches every gated repository at once, so it is dry-run on the dashboard first.
- **Value**: one maintained, pinned CI for Python and TypeScript repositories, and a GitHub Actions security gate that pull requests cannot switch off.

## Context

The org-wide repository baseline asks every code repository for a CI that lints, checks formatting and runs tests, and for a zizmor scan of its GitHub Actions files on every pull request. Most repositories are uv Python or npm TypeScript projects, so the same jobs would otherwise be copied, pinned and kept up to date in each of them.

An organization ruleset can require a workflow to pass ("Require workflows to pass before merging") without a caller file in the target repository. The ruleset references the workflow by repository, path and ref, and a public repository can only require a workflow stored in a public repository. A required workflow checks out the pull request's merge commit, so anything it reads or runs from the workspace is controlled by the pull request. Rulesets have no Evaluate mode outside GitHub Enterprise. Renovate only proposes SHA pin updates (`helpers:pinGitHubActionDigests`) for repositories that publish SemVer tags.

`zizmorcore/zizmor-action` cannot be the gate. Its default collection honours `.gitignore`, so a pull request can force-add a workflow and gitignore it: zizmor skips it, GitHub runs it. It runs `python3 -m pip` with the pull request's checkout as working directory, and `-m` puts that directory first on `sys.path`, so a pull request that adds `pip/__main__.py` can fake a passing scan. It does not expose `--strict-collection` or `--no-ignores`.

## Decision

- **The shared workflows live in `giskard-actions`.** It is public, so the required workflow can run on public repositories, and it publishes SemVer tags, so Renovate bumps the callers' pins. A separate repository would need its own release pipeline to get the same.
- **The baseline requires a CI, not these workflows.** A repository satisfies it with any CI that lints, checks formatting and tests; the Shared CI Workflows are the default way to do it. Every command is an input, passed through an environment variable to `bash -c` so no caller value is interpolated into a script.
- **Tool versions default to the project.** `uv-version` defaults to empty, so setup-uv installs the project's `required-version` or the latest uv; `python-version` defaults to empty, so uv reads `.python-version` or `requires-python`. `node-version` defaults to `26`, the Active LTS line, overridable.
- **Dependency caching is opt-in.** `enable-cache` (Python) defaults to `false` and `cache` (TypeScript) to empty: callers may run the workflows on release, tag or `workflow_run` events, where a poisoned cache restored from the default branch would run with release credentials, and prebuilt wheels and tarballs re-download quickly.
- **Composite actions are deferred.** The Shared CI Workflows cover single-job uv and npm or Yarn 1 projects. pnpm, service containers such as Postgres, and matrices are not supported; repositories that need them keep their own CI for now. Composite steps that such CIs could reuse will be designed once two repositories need the same thing.
- **The scan runs zizmor itself, from the digest-pinned image.** `zizmor-scan.yml` runs `ghcr.io/zizmorcore/zizmor:<version>@sha256:<digest>` with `docker run`, the pull request checked out into `src/` and mounted read-only, the config checked out into a sibling directory, and no network. The digest fixes the scanner bytes, and nothing the pull request contains is executed or on the scanner's path. A script rather than a `docker://` step, so exit code 3 (no inputs) becomes a pass. A hash-locked `pip install` into an isolated interpreter was rejected: the required workflow can only read files from `giskard-actions` with an extra checkout of a ref it cannot reliably know, so the hashes would live in the workflow, where Renovate cannot update them.
- **The scan audits everything, offline, without pull request ignores.** `--collect=all` ignores `.gitignore`; `--strict-collection` fails on unparsable files; `--no-ignores` drops inline `# zizmor: ignore` comments and the config's `ignore:` entries; `--offline` disables online audits, whose external advisories could turn every pull request red without a code change. Online audits belong to the weekly repo-baseline scan. It runs the default persona on `ubuntu-latest`, which has Docker.
- **Exceptions are rule-wide and merged first.** The config comes from the base commit on pull requests and merge groups, and under `--no-ignores` only `rules.<id>.disable` and rule options apply. An exception is merged to the default branch in its own pull request, behind code owner review on `.github/**`, before the pull request that needs it.
- **Only the ruleset rule is the gate.** "Require workflows to pass before merging" pins the repository, path and SHA of the workflow that must pass. A required status check matched by the name `zizmor` is not used: a pull request can satisfy it with a job of the same name in a workflow it adds or edits.
- **Rollout is progressive.** The repo-baseline dashboard first reports which repositories would fail. The ruleset targets repositories whose `zizmor-gate` custom property is `true`, set repository by repository once green, then all `kind=code` repositories. The ruleset and the property are defined in repo-baseline and applied by admins.
- **Versions move forward only.** Callers pin a release SHA and Renovate proposes the next one. A bad release is fixed forward with a patch release, never by moving or deleting a tag; a caller can stay on, or go back to, the previous SHA meanwhile. The ruleset pins `zizmor-scan.yml` by SHA; its JSON lives in repo-baseline as code (repo-baseline ADR 0001), so bumping or rolling it back is a reviewed pull request there, which Renovate can propose, applied by an admin.

## Consequences

- A `feat:` change to any shared workflow cuts a minor release that Renovate proposes to every caller; changes to `zizmor-scan.yml` also need a reviewed repo-baseline pull request that moves the ruleset SHA.
- zizmor upgrades are one monthly admin step: approve the Renovate update, release, dry-run the new version in the repo-baseline dashboard, then bump the ruleset SHA.
- In an emergency an organization owner unsets the repository's `zizmor-gate` property or disables the ruleset; nobody merges around it. Gated repositories drop any direct `workflow_call` of the scan, which would otherwise run twice.
- The job fails on symlinked `action.yml`/`action.yaml` files and on symlinks in `.github`: the runner follows them, but zizmor audits the target under its own name or skips it when it lies outside the checkout.
- Files zizmor cannot parse (test fixtures, Jinja or copier templates) fail strict collection and cannot be excepted; the dashboard dry run surfaces them before a repository is gated.
- The check shows as `zizmor` in the ruleset run and as `<caller job> / zizmor` when called as a reusable workflow. Keep the job name stable so callers' checks keep their names.
- Every pull request in a gated repository pulls the zizmor image from GHCR. The only action the scan uses is `actions/checkout`, so it is the only entry the organization actions allowlist needs; `docker run` in a script is not covered by that allowlist.
- GHCR publishes no release timestamps, so Renovate's 7-day cooldown cannot apply to the image: zizmor updates (version and digest, through a regex manager) wait for Dependency Dashboard approval, given once the same version has been on PyPI for 7 days.
- An exception disables a rule for the whole repository. Repositories trade line-level precision for exceptions a pull request cannot grant itself.
- A caller that runs the scan from `pull_request_target` scans the base branch and gets false assurance; the docs forbid it.
- This repository runs `zizmor-scan.yml` on its own pull requests next to its pedantic, online `zizmor.yml`, whose job is named `zizmor-pedantic` so only the shared scan emits `zizmor`, and which uses the same image with `--no-config` and keeps line-scoped inline ignores. `.github/zizmor.yml` holds the rule-wide exceptions the shared scan needs here.
