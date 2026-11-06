# giskard-actions

Reusable GitHub Actions automation (`Giskard-AI/giskard-actions`): a composite action for LLM-powered release notes generation, a reusable workflow for advisory Codex PR code review, and shared CI workflows.

## Language

### Shared Action Umbrella

**Consumer Repository**:
A repository that uses actions or workflows from `giskard-actions` and supplies repository-specific configuration, secrets, triggers, and job permissions.
_Avoid_: Caller repo, target repo, client repo, downstream repo

**Action Pin**:
A 40-character commit SHA paired with a SemVer version comment (`@<sha> # vX.Y.Z`) used by a **Consumer Repository** to lock every `giskard-actions` action and workflow reference to a single immutable revision.
_Avoid_: Version tag, floating ref, action ref

**Umbrella Release**:
An immutable SemVer git tag (`vX.Y.Z`) and GitHub Release published only by `release.yml` from `main` that versions every action and workflow in the repository together for Renovate discovery. There are no floating (`v0`) or per-action tags.
_Avoid_: Package version, individual action version

**Reusable Review Workflow**:
The `workflow_call` workflow `.github/workflows/codex-pr-review.yml` that runs a prepare job, one **Reviewer Job** per selected reviewer, an aggregate job and a write-only post job. The **Consumer Repository** calls it from one job that owns triggers, the comment gate, concurrency, and the permission ceiling, passes its `environment` name as an input and `AZURE_AI_API_KEY` explicitly.
_Avoid_: Codex shim, wrapper workflow

**Self-Repository Reference**:
A `uses: $/<path>` reference that resolves to the repository of the workflow being run, at the commit being run, without an `@ref`. The **Reusable Review Workflow** uses it to call its own composites at the **Action Pin**. Requires runner 2.336.0+.
_Avoid_: Local action, relative uses, `./` reference

**Trusted Action Unpack**:
The immutable directory (`github.action_path`) where GitHub Actions extracts the pinned `giskard-actions` revision on the runner, serving as the sole trusted source of executable helper scripts, prompt templates, output schemas, and the model catalog.
_Avoid_: Action checkout, helper clone, workflow ref checkout

**Consumer Config Directory**:
`.github/giskard/` in the **Consumer Repository**, holding `release-notes.json`, `codex-reviewer-map.json`, and `codex-ci-config.json`. Bundled action assets (prompts, output schema, model catalog, generic reviewer overlays) are not consumer config; they stay in `.github/codex/` of `giskard-actions`. `codex-ci-config.json` is experimental: no shipped action reads it yet.
_Avoid_: Dotfile config, root config

**Config Schema**:
A JSON Schema under `schemas/` in `giskard-actions` describing one file of the **Consumer Config Directory**, referenced from that file's `$schema` key and enforced against `examples/` by the test suite.
_Avoid_: Config spec, config template

**Trusted Config**:
The reviewer map (`.github/giskard/codex-reviewer-map.json`) and the reviewer persona and reference files it points to (`agents_dir`, default `.cursor/agents`), read strictly from the **Consumer Repository** default branch HEAD, never from the pull request head or its base commit, which a pull request author can retarget. When a persona is missing, a generic **Bundled Overlay** from `.github/codex/overlays/` is used if one exists for that reviewer name, else a generic reviewer prompt.
_Avoid_: Base-SHA overlay, PR head config, caller checkout config

**Reviewer Job**:
One job of the **Reusable Review Workflow** that runs a single Codex reviewer pass with `openai/codex-action`'s `drop-sudo`, which can only run once per job. Reviewer Jobs run as a matrix, at most `max_parallel` at a time; a failed one turns red without blocking the others or the aggregate job.
_Avoid_: Reviewer slot, reviewer step

**Key Leak Check**:
The check that fails a Codex job when the API key's value, raw or encoded, appears in any Codex output, before anything is uploaded or posted. It blocks the output rather than editing it.
_Avoid_: Secret scan, redaction

### Shared CI

**Shared CI Workflow**:
A `workflow_call` workflow (`.github/workflows/python-ci.yml` or `.github/workflows/ts-ci.yml`) that runs a **Consumer Repository**'s lint, format check and tests in `lint` and `test` jobs, with every command overridable by an input. The repository baseline requires such a CI, not the use of these workflows.
_Avoid_: CI template, starter workflow

**Required Scan Workflow**:
`.github/workflows/zizmor-scan.yml`, run on the pull requests and merge groups of code repositories by an organization ruleset ("Require workflows to pass before merging") that pins its repository, path and commit SHA, and also callable as a reusable workflow. It runs a digest-pinned zizmor image offline, with the pedantic persona, on every Actions file of the pull request, honours each **zizmor Exception** and flags the ones the pull request adds, and reads the zizmor config from the pull request's base commit. Only that ruleset rule is a trustworthy gate: a required status check matched by the name `zizmor` can be satisfied by a job the pull request adds itself. The ruleset JSON lives in repo-baseline as code, so its SHA moves through a reviewed pull request there, which Renovate can propose.
_Avoid_: Org workflow, global zizmor, zizmor shim

**zizmor Gate**:
The `zizmor-gate` boolean custom property (defined in repo-baseline) that selects the repositories the organization ruleset requires the **Required Scan Workflow** on. Admins set it repository by repository once the repo-baseline dashboard shows the repository passing, until the ruleset targets every `kind=code` repository.
_Avoid_: Evaluate mode, zizmor enforcement flag

**zizmor Exception**:
An inline `# zizmor: ignore[<rule>] -- <reason>` comment on a flagged line, reviewed in the pull request that adds it. The **Required Scan Workflow** annotates each one a pull request adds. Repository-wide policy (a rule disabled or configured in `.github/zizmor.yml`) is not an exception: it is read from the base commit.
_Avoid_: Suppression, waiver, rule-wide exception

### Release Notes Pipeline

**Project Config**:
Per-repository settings loaded from `.github/giskard/release-notes.json` in the **Consumer Repository** that define the authoritative section list, manifest paths, high-signal diff prefixes, file diffs, LLM model defaults, and deterministic upgrade rules.
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
