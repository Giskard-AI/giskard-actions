---
title: "0004: Codex review as parallel reviewer jobs"
parent: Architecture decisions
nav_order: 4
---

# Codex Review as Parallel Reviewer Jobs with drop-sudo

**Status**: accepted. Partially supersedes [ADR 0002](0002-codex-review-as-reusable-workflow.html): the single review job and the base-SHA config source.

The **Reusable Review Workflow** runs each Codex reviewer pass in its own **Reviewer Job**, as a matrix capped by a `max_parallel` input, between a prepare job and an aggregate job. Every Codex job uses `openai/codex-action`'s `safety-strategy: drop-sudo` with the `:read-only` permission profile and web search disabled. The reviewer map and personas, the **Trusted Config**, are read from the default branch HEAD, and the persona directory is configurable.

## Context

v1 ran up to 12 reviewer passes and the aggregator one after another in one job, using `safety-strategy: read-only`, which keeps `sudo`. Code running as the runner user could then read the Azure key from the memory of codex-action's proxy. `drop-sudo` closes that, but it is irreversible: a second Codex run in the same job fails.

A first fix kept the single job and ran Codex as a separate unprivileged account (`unprivileged-user`). It needed two rounds of corrections, and a review found that giving that account the runner home's group put it in the `docker` group on `ubuntu-latest`, which is root through the Docker socket.

A live probe showed `drop-sudo` with `:read-only` works on both `ubuntu-slim` and `ubuntu-latest`: Codex runs as the runner user with the `nogroup` group, no supplementary groups, no capabilities and `no_new_privs`; it cannot read the proxy's memory, the Docker socket is denied and `sudo` is gone for the rest of the job. A consumer's standalone workflow already ran one reviewer per job this way.

Reviews also found that config read from the pull request base commit can be chosen by the author, who can retarget the pull request to a branch they pushed, and that `.cursor/agents` was hard-coded although consumers may use Claude Code, Copilot or no agent tool at all.

## Decision

- **One Codex pass per job.** `prepare` (GitHub reads, no key) resolves, collects, selects and stages instructions into an artifact; `reviewer` is a matrix from `prepare`'s output with `fail-fast: false` and `max-parallel` from the input (default 4); `aggregate` runs unless the run was cancelled, keeps only outputs stamped with this review's id and valid against the reviewer schema, and replaces the rest with placeholders; `post` alone writes, without the key.
- **drop-sudo, `:read-only`, `web_search="disabled"` on every Codex pass.** Steps needing `sudo` (bubblewrap, the AppArmor sysctl) run before Codex. Self-hosted runners must opt in with `allow_self_hosted`.
- **Key Leak Check before every upload**, raw and encoded forms. A match fails the job and nothing is uploaded.
- **A failed reviewer turns its job red**, so "Re-run failed jobs" retries just that pass; the review stays advisory and its checks must not be required.
- **Trusted Config from the default branch HEAD**, never from the pull request or its base. Fork pull requests are never reviewed; this is no longer configurable.
- **Configurable personas.** The reviewer map's `agents_dir` (default `.cursor/agents`) and `references_dir` select where personas live; `<name>.md` then `<name>.agent.md`, then a bundled persona, then a generic prompt. Frontmatter is stripped; symlinks, paths leaving the repository and oversized files fail the prepare job. Without a map, only the three bundled reviewers run.
- **Write access is not defended against.** A collaborator who can push can edit a `pull_request` workflow and read the environment's secrets; drop-sudo addresses prompt injection only. This is documented rather than solved here.

## Consequences

- Major release: the `codex-pr-review` composite is removed and self-hosted runners need an opt-in. Callers otherwise keep the same `uses:` line.
- Each pass pays its own setup (bubblewrap, Codex install) and at least one billed minute, and several open pull requests multiply parallel Azure calls by `max_parallel`.
- The 15-minute `ubuntu-slim` cap now applies per pass instead of to the whole review.
- Reviewer map changes apply to open pull requests only once merged to the default branch.
