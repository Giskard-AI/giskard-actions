---
title: "0005: The zizmor Gate runs the auditor persona"
parent: Architecture decisions
nav_order: 5
---

# The zizmor Gate Runs the Auditor Persona

**Status**: accepted, 9 November 2026. Supersedes the persona part of [ADR 0003](0003-shared-ci-and-required-zizmor-workflows.md).

The **zizmor Gate** runs `scan.sh` with `--persona=auditor` instead of `pedantic`, so a job that reads a secret without declaring an Environment (`secrets-outside-env`) fails the pull request that adds it. Every secret of a code repository lives in an Environment (repo-baseline ADR 0002), and since zizmor 1.24 only the auditor persona reports this rule.

## Considered Options

- **Pedantic, with the repo-baseline dashboard watching secrets.** Rejected: a regression shows up on the dashboard after the merge, not on the pull request.
- **A second, auditor-only scan for this one rule.** Rejected: two scans to keep in step, and a zizmor config cannot turn one auditor rule on under another persona.

## Consequences

- At zizmor 1.32.0, auditor adds 24 findings to pedantic's 183 across the 63 active code repositories: 23 `secrets-outside-env` in five repositories already being fixed, and one low-confidence `artipacked` in `llm-hub-model-wrapper`. No other auditor-only rule fires.
- The auditor persona admits findings that are likely false positives, and a zizmor upgrade can add new auditor-only rules. The dashboard dry run of each upgrade shows them before the ruleset SHA moves; a justified one becomes a **zizmor Exception**.
- zizmor cannot see where a secret is stored: the Gate checks that the job declares an Environment, and the dashboard checks that no secret is stored at repository level.
