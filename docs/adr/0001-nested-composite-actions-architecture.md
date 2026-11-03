---
title: "0001: Nested composite actions and umbrella versioning"
parent: Architecture decisions
nav_order: 1
---

# Nested Composite Actions Architecture and Umbrella Versioning

**Status**: accepted, partially superseded by [ADR 0002](0002-codex-review-as-reusable-workflow.html) (Codex review now ships as a reusable workflow; the release-notes action moved to `.github/actions/release-notes/action.yml`)

`Giskard-AI/giskard-actions` ships shared automation to **Consumer Repositories** as composite actions (`action.yml`, `.github/actions/codex-pr-review/action.yml`, and `.github/actions/codex-post-review/action.yml`) rather than reusable workflows (`workflow_call`), executes Codex orchestration scripts on the runner's system Python 3.12 (`ubuntu-24.04`) while installing the `generate-release-notes` CLI via `uv tool install --constraints`, and versions the entire repository under a single automatic SemVer **Umbrella Release** tag (`vX.Y.Z`) cut on pushes to `main` that contain releasable commits.

## Considered Options

- **Reusable workflows (`workflow_call`) vs. composite actions**: Reusable workflows cannot inherit or let the caller job configure the GitHub `environment:` (such as `environment: ci`), force a separate job startup before evaluating comment gates, and previously required a GitHub App token to re-clone helper scripts. Composite actions run as steps inside the **Consumer Repository**'s own job, allowing the caller to own `environment: ci`, job-level `if:` conditions, and split read-only analysis jobs from `pull-requests: write` comment-posting jobs while running helper scripts directly from the **Trusted Action Unpack** (`github.action_path`).
- **System Python 3.12 for Codex helper vs. managed Python/uv**: `.github/actions/codex-pr-review` and `.github/actions/codex-post-review` invoke `scripts/ci/codex_pr_assessment.py` using the `ubuntu-latest` (`ubuntu-24.04`) runner's preinstalled Python 3.12 without a `setup-uv` step, keeping the Codex helper strictly stdlib-only for zero-install startup and avoiding Python 3.15 source builds for compiled Rust dependencies (`pydantic-core`, `jiter`) used by `generate-release-notes`.
- **Single Umbrella Release tag (`vX.Y.Z`) vs. per-action tags or branch tracking**: Mend Renovate's `github-actions` manager requires SemVer git tags (`vX.Y.Z`) to propose digest updates (`@<sha> # vX.Y.Z`) across private organization repositories. Cutting a single SemVer **Umbrella Release** tag from `main` (without mutating `pyproject.toml`) lets Renovate group all `Giskard-AI/giskard-actions` references in a **Consumer Repository** into one atomic **Action Pin** update.
