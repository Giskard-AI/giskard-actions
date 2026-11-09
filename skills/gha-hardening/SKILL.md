---
name: gha-hardening
disable-model-invocation: true
description: >-
  Harden one repository's GitHub Actions until it passes the Giskard zizmor
  Gate, and audit its secrets, rulesets and GitHub Apps. Use to fix zizmor
  findings, pin actions, scope secrets, or replace a token with a GitHub App.
---

# Harden a repository's GitHub Actions

The **Gate** is the org ruleset that runs `zizmor-scan.yml` from `Giskard-AI/giskard-actions` on every pull request of a code repository. This skill brings one repository to a passing Gate in one draft pull request, and reports what only a human can change: secrets, Apps, rulesets.

Threat models, fix samples and pitfalls: [reference.md](reference.md).

## Rules

- **One source of truth for the scan**: `scripts/gate-scan.sh` runs the Gate's own script at the Gate's commit. A pass there is a pass on the pull request. Never judge with another zizmor version or flags.
- **Fix, never silence.** Each `# zizmor: ignore[<rule>] -- <reason>` is a human decision: ask when a human is in the conversation; otherwise propose it as described in step 4.
- **Keep every existing comment** in the files you edit.
- **Read-only outside the branch.** Settings, secrets, rulesets and Apps change through the human steps of the pull request, never from this run.

## Steps

### 1. Audit

```bash
bash <skill>/scripts/audit.sh OWNER/REPO <clone>
```

Done when you have its full output. Without Docker it runs zizmor with `uvx`. A section the token cannot read is reported as such; carry it to the pull request as is.

### 2. Fix the findings

Work file by file, using the fix table in [reference.md](reference.md#fix-table). Pin every action with:

```bash
bash <skill>/scripts/pin-action.sh actions/checkout@v4   # → actions/checkout@<sha> # v4.4.0
```

Rerun after each batch:

```bash
bash <skill>/scripts/gate-scan.sh OWNER/REPO <clone>
```

Done when it exits 0, or every remaining finding is one a fix cannot remove without changing what the workflow does. Existing ignore comments stay as they are.

Check the changes the Gate does not see:

- **Job names**: a new `name:` renames the status check. A job whose check is listed as "required check" in the audit takes its current check name as `name:`; other jobs get a short name a reviewer reads in the checks list.
- **The repository's own zizmor job**: keep it while the audit shows "Gate ruleset: not active yet" (it must pass too, in step 4); once the Gate is active, delete it (the Gate replaces it).
- **Renovate**: the config extends `github>Giskard-AI/.github`, without the presets it already includes (`config:recommended`, `helpers:pinGitHubActionDigests`). Without a config, add `renovate.json` with only that `extends`.

### 3. Verify locally

Done when both hold:

- `gate-scan.sh` exits 0 (counting step 4's proposed ignores, if any).
- `git diff` changes only `.github/`, `action.yml` files and the Renovate config, and drops no comment.

### 4. Open a draft pull request

Branch `ci/zizmor-gate`, title `ci: pass the zizmor Gate`. Body sections, each omitted when empty:

1. **Fixed**: findings by rule, counts before → after, and the last line of `gate-scan.sh` with the giskard-actions ref it printed (while the Gate is not active, that output is the evidence).
2. **Decisions needed**: each remaining finding with file:line, why zizmor flags it, and your recommendation. Without a human in the conversation, add the recommended ignores in one last commit, `ci: propose zizmor ignores (needs a decision)`, so the reviewer accepts or rejects each one; the pull request stays a draft until then.
3. **Human steps**, from the audit, each one a command or link to run:
   - Repository secrets (target: none): move each to an Environment deployable from the branch that uses it.
   - A personal access token, or an App with more access than the job needs: a dedicated App, created with `python3 <skill>/scripts/setup_github_app.py --dry-run ...` filled in (the wizard prints its plan; drop `--dry-run` to apply).
   - `SENSITIVE` Apps and `always` bypass actors from the audit: list them for an org admin, with the link the audit printed.
   - A Renovate config added while the `renovate` App is installed on selected repositories: add this repository to it.
   - Required checks renamed by this pull request.

Done when the draft pull request is open and its checks pass: `gh pr checks <pr> --watch` exits 0.
