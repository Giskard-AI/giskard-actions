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

- Tags `vX.Y.Z` and GitHub Releases are created only by the `release.yml` workflow on pushes to `main`. Conventional commits since the last tag pick the bump: `fix` or `perf` is a patch, `feat` a minor, and a `!` header or a `BREAKING CHANGE:` footer line a major (a minor while the version is `0.x`). A range with none of these (only `chore`, `docs`, `ci`, ...) creates no release.
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
- **Sandboxed model execution.** Codex runs in a read-only bubblewrap sandbox; the workflow runs `codex sandbox -- true` before the first pass and fails if the sandbox cannot start. See [residual risks](codex-pr-review.html#residual-risks).
- **Untrusted input stays data.** Only comments, issues and pull requests authored by owners, members and collaborators reach the models, and model output is sanitized (images, raw HTML, invisible characters and untrusted links removed) before it is published.

Report vulnerabilities privately; see [SECURITY.md](https://github.com/Giskard-AI/giskard-actions/blob/main/SECURITY.md).

## Migrating from v0.3.x

v0.4.0 moves every action under `.github/actions/` and every consumer file under `.github/giskard/`. There are no compatibility shims: if a legacy config file is still in place and the new one is missing, the run fails with an error naming the new path.

| v0.3.x | v0.4.0 |
| --- | --- |
| `uses: Giskard-AI/giskard-actions@<sha>` | `uses: Giskard-AI/giskard-actions/.github/actions/release-notes@<sha>` |
| `.release-notes.json` | `.github/giskard/release-notes.json` |
| `.github/codex/reviewer-map.json` | `.github/giskard/codex-reviewer-map.json` |
| Two jobs calling the `codex-pr-review` and `codex-post-review` composites | One job calling `.github/workflows/codex-pr-review.yml` (see [Caller workflow](codex-pr-review.html#caller-workflow)) |

```bash
git mv .release-notes.json .github/giskard/release-notes.json
git mv .github/codex/reviewer-map.json .github/giskard/codex-reviewer-map.json
```

When you switch to the reusable workflow, move the concurrency group from workflow level to the calling job and pass `AZURE_AI_API_KEY` under `secrets:`.
