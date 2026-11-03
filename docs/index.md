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

Both read their per-repository settings from `.github/giskard/` in your repository. See [Configuration](configuration.html) for the files and their JSON Schemas.

## Pinning and versioning

The repository has **one SemVer version** for everything it ships.

- Tags `vX.Y.Z` and GitHub Releases are created only by the `release.yml` workflow on pushes to `main`. Conventional commit types pick the bump: `fix` is a patch, `feat` a minor, and `!` or `BREAKING CHANGE` a major.
- There are no floating tags (`v0`) and no per-action tags.
- Pin the **full 40-character commit SHA** with a version comment, and use the same SHA for every reference to this repository:

  ```yaml
  uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<full-commit-sha> # v0.4.0
  ```

- [Renovate](https://docs.renovatebot.com/) with the `helpers:pinGitHubActionDigests` preset proposes SHA and comment updates when a new release is published.

## Security model

- **Trusted code is the pinned unpack.** Helper scripts, prompts, output schemas and the Azure model catalog run from the directory GitHub unpacks at your pinned SHA. The reusable workflow references its own composites with the self-repository syntax `$/`, so they resolve to that same commit.
- **Reviewer configuration comes from the pull request base SHA**, never the PR head. A pull request cannot change the reviewer map or personas used to review it.
- **Least privilege.** Every workflow starts from `permissions: {}`. The Codex review job only reads; a separate job with `pull-requests: write` and no Azure credentials posts the review.
- **Sandboxed model execution.** Codex runs in a read-only bubblewrap sandbox; the workflow installs and probes bubblewrap before the first pass and fails if the sandbox cannot start.

Report vulnerabilities privately; see [SECURITY.md](https://github.com/Giskard-AI/giskard-actions/blob/main/SECURITY.md).
