# Glossary — `giskard-release-notes`

Ubiquitous language for the release notes pipeline (`src/release_notes/`).

| Term | Code construct | Definition |
|---|---|---|
| **Project Config** | `ProjectConfig` (`config.py`) | Per-repository settings loaded from `.release-notes.json` (or `DEFAULT_SECTIONS` when absent). Defines authoritative section order, manifest paths, high-signal diff prefixes, file diffs, and deterministic upgrade rules. |
| **Thematic Section** | `SectionConfig` in `what_changed_section_ids()` | An LLM-populated section under `## What changed` (e.g. `features`, `fixes`, `ux_frontend`, `api_backend`, `python_api`, `probes`). When `sections` is non-empty in `.release-notes.json`, only listed sections are active. |
| **Synthesis Section** | `SectionConfig` in `llm_section_ids()` | Any enabled section filled by the Stage 3 synthesis LLM (`summary`, `upgrade_notes`, and all enabled **Thematic Sections**). |
| **Deterministic Section** | `DETERMINISTIC_SECTION_IDS` (`deps`, `appendix`) | Sections rendered strictly from git and manifest diffs without LLM synthesis. |
| **Commit Category** | `ProjectConfig.commit_category_ids()` | Controlled vocabulary for Stage 2 (`consolidate_commit`) commit classification: the repository's enabled **Thematic Section** IDs plus `"other"` (for internal chores, tests, and refactors). |
| **Tag Range** | `TagRange` (`models.py`) | The `(previous_tag, current_tag)` git boundary for a release. In `--dry-run` mode, if `v{version}` does not exist (such as `v0.0.0-pr`), falls back to `(latest_v_tag, "HEAD")`. |
| **Commit Digest** | `CommitDigest` (`models.py`) | Stage 1 per-commit bundle containing commit metadata, PR number, Linear/GitHub issue references, diff stats, and truncated file diffs prioritized by `high_signal_prefixes`. |
| **Consolidated Commit** | `ConsolidatedCommit` (`models.py`) | Stage 2 per-commit structured LLM output: single primary `category` (from **Commit Category**), `short_name`, `consolidated_description`, `why`/`context`, `upgrade_notes`, and `dependency_updates`. |
| **Upgrade Rule** | `UpgradeRule` (`config.py`, `upgrade.py`) | Deterministic path-matching rule in `.release-notes.json` that emits an upgrade note whenever matching files change in the **Tag Range**, merged ahead of LLM-inferred upgrade notes. |
| **Release Notes Payload** | `ReleaseNotesPayload` / `build_release_notes_payload_model()` | Stage 3 structured LLM output whose fields dynamically match the repository's enabled **Synthesis Sections**. |
