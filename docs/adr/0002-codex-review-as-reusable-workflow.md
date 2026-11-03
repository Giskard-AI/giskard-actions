---
title: "0002: Codex review as a reusable workflow"
parent: Architecture decisions
nav_order: 2
---

# Codex Review as a Reusable Workflow with Self-Repository References

**Status**: accepted. Partially supersedes [ADR 0001](0001-nested-composite-actions-architecture.html).

Codex PR review now ships as the **Reusable Review Workflow** `.github/workflows/codex-pr-review.yml` (`on: workflow_call`), which wraps the `codex-pr-review` and `codex-post-review` composite actions through **Self-Repository References** (`uses: $/.github/actions/<name>`). Release notes stays a composite action, moved to `.github/actions/release-notes/action.yml` so every action lives under `.github/actions/<name>/`. The repository keeps one SemVer **Umbrella Release** version, and consumer settings move to the **Consumer Config Directory** `.github/giskard/`, each file described by a published **Config Schema**.

## Context

ADR 0001 rejected reusable workflows because the caller could not set the job `environment:` and the called workflow had to re-clone its own helpers at the right commit. Every consumer therefore copied a two-job shim (read-only review, write-only post) with the environment, permissions, artifact hand-off and composite references, and had to keep all pins in sync.

GitHub's self-repository syntax (`$/`, available since 2026-07-30 on runner 2.336.0+) resolves action references inside a called workflow to the called workflow's own repository at the commit being run, without an `@ref`. A pinned reusable workflow can now call its own composites at exactly the pinned commit.

## Decision

- **Codex review is a reusable workflow.** The caller passes the environment name as the `environment` input, and the called `review` job sets its `environment:` name from that input (with `deployment: false`). That resolves ADR 0001's concern: environment secrets and `vars` from the caller's environment are available in the called job. The secret is passed explicitly (`AZURE_AI_API_KEY`); `secrets: inherit` is never used.
- **The caller still owns the gate.** Triggers, the `/review` comment gate, concurrency and the permission ceiling stay on the calling job. The reusable workflow declares `permissions: {}` and grants per job: the review job reads, the post job alone writes the PR review.
- **Release notes stays a composite action.** Callers often mint their own submodule checkout or GitHub App tokens in the same job before generating notes, which a separate called job cannot share.
- **One SemVer for the repository.** Tags come only from `release.yml`. There are no floating or per-action tags; consumers pin the full commit SHA with a `# vX.Y.Z` comment.
- **Consumer configuration lives in `.github/giskard/`** (`release-notes.json`, `codex-reviewer-map.json`, `codex-ci-config.json`) with JSON Schemas under `schemas/`. Bundled action assets (prompts, output schema, model catalog) stay in `.github/codex/`. The reviewer map is still read from the pull request base SHA.

## Consequences

- Consumers replace the two-job shim with one job and one pin.
- Self-hosted runners must run 2.336.0 or later. actionlint 1.7.12 does not parse `$/` yet (rhysd/actionlint#732, #760); this repository's lint run ignores those two messages.
- The review job's runner and timeout are inputs. The default `ubuntu-slim` runner is capped at 15 minutes by GitHub, so `timeout_minutes` defaults to 15 and large reviews pass `runner: ubuntu-latest` with a larger timeout. The composite preflights `codex sandbox -- true` and relaxes Ubuntu 24.04's AppArmor user-namespace restriction on hosted runners so both runners work.
- The move to `.github/giskard/` is a breaking change for existing consumers, which currently pin v0.3.1. There are no compatibility shims; instead, a legacy config file left in place (`.release-notes.json`, `.github/codex/reviewer-map.json`) fails the run with an error naming the new path, and the README documents the migration.
