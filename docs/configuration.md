---
title: Configuration
nav_order: 5
---

# Configuration
{: .no_toc }

Per-repository settings live in `.github/giskard/` of the repository that uses the actions. Each file has a JSON Schema published in [`schemas/`](https://github.com/Giskard-AI/giskard-actions/tree/main/schemas). Add a `$schema` key for editor validation and completion. Each schema's `$id` points at `main`, which can be ahead of the release you pin; reference the version tag you pin instead:

```json
{
  "$schema": "https://raw.githubusercontent.com/Giskard-AI/giskard-actions/v1.0.0/schemas/release-notes.schema.json"
}
```

| File | Used by | Schema |
| --- | --- | --- |
| `.github/giskard/release-notes.json` | Release notes | [`release-notes.schema.json`](https://github.com/Giskard-AI/giskard-actions/blob/main/schemas/release-notes.schema.json) |
| `.github/giskard/codex-reviewer-map.json` | Codex PR review | [`codex-reviewer-map.schema.json`](https://github.com/Giskard-AI/giskard-actions/blob/main/schemas/codex-reviewer-map.schema.json) |
| `.github/giskard/codex-ci-config.json` | Codex CI triage helper (experimental, internal) | [`codex-ci-config.schema.json`](https://github.com/Giskard-AI/giskard-actions/blob/main/schemas/codex-ci-config.schema.json) |

Ready-to-copy samples are in [`examples/`](https://github.com/Giskard-AI/giskard-actions/tree/main/examples).

1. TOC
{:toc}

## Release notes

`.github/giskard/release-notes.json`. Every field is optional; without the file the action uses the default sections `summary`, `features`, `fixes` and `misc`.

| Field | Description |
| --- | --- |
| `github_repository` | `owner/repo` for compare URLs and GitHub enrichment (defaults to `GITHUB_REPOSITORY`) |
| `trusted_github_authors` | Extra authors whose issues and pull requests are enriched, as `{"login", "id"}` entries matched on the numeric `id` (`gh api users/<login> --jq .id`). Owners, members and collaborators are always trusted, but GitHub reports private org members and former members to workflow tokens as `CONTRIBUTOR` or `NONE`; list them here. Do not list dependency bots: their bodies quote third-party changelogs |
| `manifest_paths` | `pyproject.toml`, `package.json` or `pom.xml` files diffed for the dependency table |
| `high_signal_prefixes` | Path prefixes whose diffs are prioritized in commit digests |
| `file_diffs` | Paths whose tag-range diff is embedded in the notes |
| `ignored_basenames` | Basenames excluded from stats (default `uv.lock`, `pnpm-lock.yaml`) |
| `ignored_path_prefixes` | Path prefixes excluded from stats |
| `sections` | Ordered output sections: `id`, optional `title`, `description` (LLM writing instructions) and `enabled` |
| `upgrade_rules` | Deterministic upgrade notes: `id`, `short_name`, `description`, plus `paths`, `path_prefixes` and optional `filename_keywords` |
| `model`, `effort` | Default model and reasoning effort |
| `consolidation_model`, `consolidation_effort`, `synthesis_model`, `synthesis_effort` | Per-stage overrides |
| `llm_concurrency` | Maximum parallel LLM calls (default 10) |

Built-in section ids: `summary`, `features`, `fixes`, `misc`, `ux_frontend`, `api_backend`, `python_api`, `infra_deps`, `upgrade_notes`, `deps`, `appendix`. Any other identifier creates a custom section. `deps` and `appendix` are rendered from git without the LLM. `upgrade_notes` is hybrid: rule-based notes always appear, and LLM notes are kept when their `short_name` is new.

Examples: [`web-app`](https://github.com/Giskard-AI/giskard-actions/blob/main/examples/web-app.release-notes.json), [`python-library`](https://github.com/Giskard-AI/giskard-actions/blob/main/examples/python-library.release-notes.json), [`minimal`](https://github.com/Giskard-AI/giskard-actions/blob/main/examples/minimal.release-notes.json).

## Codex reviewer map

`.github/giskard/codex-reviewer-map.json`, read from the **default branch HEAD**, together with the personas it points to. Changes to it only take effect after they merge.

| Field | Description |
| --- | --- |
| `rules` | Required, non-empty. Each rule selects one reviewer |
| `rules[].name` | Reviewer name (`[a-zA-Z0-9_-]+`); see the persona lookup below |
| `rules[].model_kind` | `normal` (default) or `critical`; selects the model and effort defaults |
| `rules[].model`, `rules[].effort` | Per-rule overrides |
| `rules[].path_prefixes`, `rules[].path_suffixes` | Prefix and suffix matchers; when both are set, both must match |
| `rules[].path_names` | Exact paths or basenames |
| `rules[].path_contains` | Case-insensitive substrings |
| `rules[].path_excludes` | Path prefixes the rule never matches, e.g. `tests/probes/` in a rule on `tests/` |
| `rules[].role_references` | File names (`*.md`) in `references_dir` given to the reviewer |
| `agents_dir` | Directory of reviewer personas; default `.cursor/agents`. For example `.claude/agents` or `.github/agents` |
| `references_dir` | Directory of `role_references`; default `<agents_dir>/references` |
| `defaults` | `normal_model`, `normal_effort`, `critical_model`, `critical_effort`, `aggregator_model`, `aggregator_effort` |

A reviewer is selected when any changed file matches one of its rules. When several rules select the same reviewer, a `critical` match is never downgraded to `normal`, and their `role_references` are combined. At most 12 reviewers run per pull request. Workflow inputs override `defaults`.

Persona lookup for a reviewer `<name>`: `<agents_dir>/<name>.md`, then `<agents_dir>/<name>.agent.md`, then a persona bundled with the workflow (`security-auditor`, `infra-reviewer`, `prompt-critic`), then a generic reviewer prompt. YAML frontmatter is stripped. `agents_dir` and `references_dir` are relative paths inside the repository whose segments match `[A-Za-z0-9._-]+`, without `..` or `.git`. Persona and reference files must be regular files (no symlinks) of at most 30,000 characters.

Without a reviewer map, only the three bundled reviewers are selected; see [Codex PR review](codex-pr-review.html#reviewer-personas).

Example: [`codex-reviewer-map.json`](https://github.com/Giskard-AI/giskard-actions/blob/main/examples/codex-reviewer-map.json).

## Codex CI config

{: .warning }
Experimental and internal. No action or workflow shipped by this repository reads this file yet; the format may change in any release.

`.github/giskard/codex-ci-config.json`, used by the CI triage helper (`collect-ci` and `triage-ci` in `scripts/ci/codex_pr_assessment.py`) through the `CODEX_CI_CONFIG` environment variable.

| Field | Description |
| --- | --- |
| `key_workflows` | Workflow names that must have a finished, successful run on the head SHA |
| `recommendations[].action`, `recommendations[].target` | Required. For example `run_workflow` or `apply_label` and the workflow or label name |
| `recommendations[].reason` | Message shown in the triage output |
| `recommendations[].when` | `package_related` matches manifest and lockfile changes |
| `recommendations[].when_paths`, `recommendations[].when_path_prefixes` | Changed paths that trigger the rule |
| `recommendations[].missing_workflow`, `recommendations[].missing_any_workflows` | Skip the rule when these workflows already ran |

Example: [`codex-ci-config.json`](https://github.com/Giskard-AI/giskard-actions/blob/main/examples/codex-ci-config.json).

## Validating locally

```bash
uvx check-jsonschema \
  --schemafile https://raw.githubusercontent.com/Giskard-AI/giskard-actions/v1.0.0/schemas/release-notes.schema.json \
  .github/giskard/release-notes.json
```

This repository's test suite validates every file in `examples/` and its own `.github/giskard/` against these schemas.
