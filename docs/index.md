---
title: Home
layout: home
nav_order: 1
---

# giskard-actions

Reusable GitHub Actions automation from [Giskard](https://github.com/Giskard-AI).

| What | Form | Use it from |
| --- | --- | --- |
| [Release notes](release-notes.html) | Composite action | `Giskard-AI/giskard-actions/.github/actions/release-notes@<sha>` |
| [Codex PR review](codex-pr-review.html) | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/codex-pr-review.yml@<sha>` |
| [Python CI](ci-workflows.html#python-ci) | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/python-ci.yml@<sha>` |
| [TypeScript CI](ci-workflows.html#typescript-ci) | Reusable workflow | `Giskard-AI/giskard-actions/.github/workflows/ts-ci.yml@<sha>` |
| [zizmor scan](ci-workflows.html#zizmor-scan) | Reusable and organization-required workflow | `Giskard-AI/giskard-actions/.github/workflows/zizmor-scan.yml@<sha>` |

Release notes and Codex PR review read their per-repository settings from `.github/giskard/` in your repository. See [Configuration](configuration.html) for the files and their JSON Schemas. The [shared CI workflows](ci-workflows.html) take inputs only.

## Pinning and versioning

The repository has **one SemVer version** for everything it ships.

- Tags `vX.Y.Z` and GitHub Releases are created only by the `release.yml` workflow on pushes to `main`. Conventional commits since the last tag pick the bump: `fix`, `perf` or any type with the `deps` scope (for example `chore(deps): ...` from Renovate, including its weekly lock file maintenance) is a patch, `feat` a minor, and a `!` header or a `BREAKING CHANGE:` footer line a major (a minor while the version is `0.x`). Merged dependency updates therefore always publish a patch release, so runtime and security updates reach consumers. A range with none of these (only `chore`, `docs`, `test`, `ci`, ... without the `deps` scope) creates no release. Maintainers can run `release.yml` manually on `main` with a `version` input to release an explicit `X.Y.Z` instead (for example the first `1.0.0`); it must be higher than every existing tag.
- There are no floating tags (`v0`) and no per-action tags.
- The version lives only in the git tags. `version` in `pyproject.toml` stays `0.0.0` and is never bumped.
- Pin the **full 40-character commit SHA** of a [release](https://github.com/Giskard-AI/giskard-actions/releases) with a version comment, and use the same SHA for every reference to this repository:

  ```yaml
  uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<full-commit-sha> # vX.Y.Z
  ```

- [Renovate](https://docs.renovatebot.com/) with the `helpers:pinGitHubActionDigests` preset proposes SHA and comment updates when a new release is published.

## Security model

- **Trusted code is the pinned unpack.** Helper scripts, prompts, output schemas and the Azure model catalog run from the directory GitHub unpacks at your pinned SHA. The reusable workflow references its own composites with the self-repository syntax `$/`, so they resolve to that same commit.
- **Reviewer configuration comes from the default branch HEAD**, never the PR head or its base commit. A pull request cannot change or retarget the reviewer map or personas used to review it.
- **Least privilege.** Every workflow starts from `permissions: {}`. Only the Codex prepare job reads GitHub, only reviewer and aggregate jobs hold the Azure key, and a separate job with `pull-requests: write` and no Azure credentials posts the review.
- **Sandboxed model execution.** Each Codex pass runs in its own job with sudo dropped, in a read-only bubblewrap sandbox without web search, and its output is checked for the key before upload. See the [security model](codex-pr-review.html#security-model).
- **Untrusted input stays data.** Only comments, issues and pull requests authored by owners, members and collaborators (or, for release notes, accounts listed by user id in `trusted_github_authors`) reach the models, and model output is sanitized (images, raw HTML, invisible characters and untrusted links removed) before it is published.

Report vulnerabilities privately; see [SECURITY.md](https://github.com/Giskard-AI/giskard-actions/blob/main/SECURITY.md).
