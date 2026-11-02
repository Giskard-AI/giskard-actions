# giskard-actions

Shared GitHub composite actions (`Giskard-AI/giskard-actions`) for Giskard repositories, providing LLM-powered release notes generation and advisory Codex PR code review.

## Language

### Shared Action Umbrella

**Consumer Repository**:
A Giskard repository (such as `llm-hub` or `lidar`) that invokes composite actions from `giskard-actions` and supplies repository-specific configuration, secrets, and job permissions.
_Avoid_: Caller repo, target repo, client repo, downstream repo

**Action Pin**:
A 40-character commit SHA paired with a SemVer version comment (`@<sha> # vX.Y.Z`) used by a **Consumer Repository** to lock all `giskard-actions` composite action references to a single immutable revision.
_Avoid_: Version tag, floating ref, action ref

**Umbrella Release**:
An immutable SemVer git tag (`vX.Y.Z`) and GitHub Release published automatically from `main` in `giskard-actions` that versions all composite actions in the repository together for Renovate discovery.
_Avoid_: Package version, individual action version

**Trusted Action Unpack**:
The immutable directory (`github.action_path`) where GitHub Actions extracts the pinned `giskard-actions` revision on the runner, serving as the sole trusted source of executable helper scripts and prompt templates.
_Avoid_: Action checkout, helper clone, workflow ref checkout

**Base-SHA Overlay**:
Trusted configuration and reviewer persona files (`.github/codex/reviewer-map.json` and `.cursor/agents/*.md`) fetched strictly from the **Consumer Repository** pull request base commit rather than the untrusted pull request head.
_Avoid_: PR head config, caller checkout config

### Release Notes Pipeline

**Project Config**:
Per-repository settings loaded from `.release-notes.json` in the **Consumer Repository** that define the authoritative section list, manifest paths, high-signal diff prefixes, file diffs, LLM model defaults, and deterministic upgrade rules.
_Avoid_: Repo settings, release config

**Thematic Section**:
A domain-specific section under `What changed` (such as `features`, `fixes`, `ux_frontend`, `api_backend`, or `python_api`) declared in **Project Config** and populated by LLM synthesis.
_Avoid_: Category bucket, body section

**Synthesis Section**:
Any enabled release notes section generated during Stage 3 LLM synthesis, comprising `summary`, `upgrade_notes`, and all enabled **Thematic Sections**.
_Avoid_: LLM section

**Deterministic Section**:
A release notes section (`deps` or `appendix`) rendered strictly from git history and manifest diffs without LLM synthesis.
_Avoid_: Static section, non-LLM section

**Commit Category**:
The controlled classification vocabulary for Stage 2 commit consolidation, consisting of the **Consumer Repository**'s enabled **Thematic Section** identifiers plus `other` (for internal chores, tests, and refactors).
_Avoid_: Commit type, conventional commit prefix

**Tag Range**:
The `(previous_tag, current_tag)` git boundary over which commits and manifest diffs are collected for a release, falling back to `(latest_v_tag, HEAD)` during dry-run previews when the target tag does not yet exist.
_Avoid_: Commit range, diff window

**Commit Digest**:
The Stage 1 per-commit bundle containing commit metadata, pull request and Linear/GitHub issue references, diff statistics, and truncated file diffs prioritized by high-signal path prefixes.
_Avoid_: Raw commit, commit payload

**Consolidated Commit**:
The Stage 2 per-commit structured summary produced by the LLM from a **Commit Digest** and enriched issue context, assigning a single primary **Commit Category**, a short name, a consolidated description, rationale, upgrade notes, and dependency updates.
_Avoid_: Commit summary, enriched commit

**Upgrade Rule**:
A deterministic path-matching rule in **Project Config** that emits an upgrade note whenever matching files change within the **Tag Range**, merged ahead of LLM-inferred upgrade notes.
_Avoid_: Breaking change heuristic

**Release Notes Payload**:
The Stage 3 structured LLM output whose schema dynamically matches the **Consumer Repository**'s enabled **Synthesis Sections** before markdown rendering.
_Avoid_: Release notes draft, synthesis response
