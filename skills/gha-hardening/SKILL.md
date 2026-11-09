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

- **One source of truth for the scan**: `scripts/gate-scan.sh` runs the Gate's own script at the Gate's commit. A pass there is a pass on the pull request. Judge only by its exit status; its "suppressed" count is findings of stricter personas, outside the Gate, and its auto-fix notes are for humans.
- **Fix, never silence.** Each `# zizmor: ignore[<rule>] -- <reason>` is a human decision, whatever the scan's failure message suggests: ask when a human is in the conversation; otherwise propose it as described in step 4.
- **Keep every existing comment** in the files you edit.
- **Read-only outside the branch.** Settings, secrets, rulesets and Apps change through the human steps of the pull request, never from this run.

## Steps

### 1. Audit

```bash
bash <skill>/scripts/audit.sh OWNER/REPO <clone>
```

Done when it exits 0 and you have its full output (exit 2 means GitHub was unreachable: fix that first). Without Docker it runs zizmor with `uvx`. A section the token cannot read is reported as such; carry it to the pull request as is.

Also note which checks fail on the default branch today (`gh run list -R OWNER/REPO --branch <default> --limit 5`), so step 4 can tell them apart.

### 2. Fix the findings

Create branch `ci/zizmor-gate`, then work file by file, using the fix table in [reference.md](reference.md#fix-table). Pin actions with:

```bash
bash <skill>/scripts/pin-action.sh actions/checkout@v4   # → actions/checkout@<sha> # v4.4.0
```

Rerun after each batch:

```bash
bash <skill>/scripts/gate-scan.sh OWNER/REPO <clone>
```

Done when it exits 0, or every remaining finding is one a fix cannot remove without changing what the workflow does. Existing ignore comments and existing pins the scan does not flag stay as they are.

Check the changes the Gate does not see:

- **Job names**: a new `name:` renames the status check. A job whose check is listed as "required check" in the audit takes its current check name as `name:`; other jobs take their key in sentence case (`build` → `Build`).
- **The repository's own zizmor job**: keep it while the audit shows "Gate ruleset: not active yet" (it must pass too, in step 4); once the Gate is active, delete it (the Gate replaces it).
- **Renovate**: the config extends `github>Giskard-AI/.github`, without the presets it already includes (`config:recommended`, `helpers:pinGitHubActionDigests`); other settings stay. Without a config, add `renovate.json`: `{"$schema": "https://docs.renovatebot.com/renovate-schema.json", "extends": ["github>Giskard-AI/.github"]}`.

### 3. Verify locally

Done when both hold:

- `gate-scan.sh` exits 0 (counting step 4's proposed ignores, if any).
- `git diff` changes only `.github/`, `action.yml` files and the Renovate config, and drops no comment.

### 4. Open a draft pull request

Commits: `ci: pass the zizmor Gate` for the workflows, `chore(renovate): extend the shared preset` for Renovate. Title `ci: pass the zizmor Gate`. Body sections, each omitted when empty:

1. **Fixed**: findings by rule, counts before → after, and the scan's first line (the giskard-actions ref) and summary line (while the Gate is not active, that output is the evidence).
2. **Decisions needed**: each remaining finding with file:line, why zizmor flags it, and your recommendation. Without a human in the conversation, add the recommended ignores in one last commit, `ci: propose zizmor ignores (needs a decision)`, so the reviewer accepts or rejects each one; the pull request stays a draft until then.
3. **Checks already failing**: each check that fails on the default branch too, with the run that shows it. Fixing it is outside this pull request.
4. **Human steps**, from the audit, each one a command or link to run:
   - Repository secrets used only by release or deploy jobs (default branch, tags): move each to an Environment deployable from that branch. Secrets pull request jobs need stay where they are.
   - Secrets no workflow references: delete them.
   - A personal access token or deploy key, or an App with more access than the job needs: a dedicated App, created with `python3 <skill>/scripts/setup_github_app.py --dry-run ...` filled in (`--install-repo` for another repository the job reads; the wizard prints its plan; drop `--dry-run` to apply).
   - A Renovate config added while the `renovate` App is installed on selected repositories: add this repository to it.
   - Required checks renamed by this pull request.

   Org-wide findings of the audit (`SENSITIVE` Apps, organization ruleset bypass) belong to the repo-baseline dashboard; leave them out.

Done when the draft pull request is open and `gh pr checks <pr> --watch` shows every check passing except those listed under "Checks already failing".
