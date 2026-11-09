---
name: gha-hardening
disable-model-invocation: true
description: >-
  Harden one repository's GitHub Actions until it passes the Giskard zizmor
  Gate, and move its secrets into Environments. Use to fix zizmor findings,
  pin actions, scope secrets, or replace a token with a GitHub App.
---

# Harden a repository's GitHub Actions

The **Gate** is the org ruleset that runs `zizmor-scan.yml` from `Giskard-AI/giskard-actions`, with the auditor persona, on every pull request of a code repository. This skill brings one repository to a passing Gate in one draft pull request, and lists the **Human Steps** (settings changes) a person applies before merging it. Terms are defined in `GLOSSARY.md` of `giskard-actions`.

Threat models, fix samples and pitfalls: [reference.md](reference.md).

## Rules

- **One source of truth for the scan**: `scripts/gate-scan.sh` runs the Gate's own script at the Gate's commit. Judge only by its exit status: a pass there is a pass on the pull request.
- **Fix first.** A **zizmor Exception** (`# zizmor: ignore[<rule>] -- <reason>`) is a human decision, whatever the scan's message suggests: ask when a human is in the conversation; otherwise propose it as described in step 4.
- **Every secret lives in a Secret Environment**: one per kind of job, declared by every job that reads one of its secrets, and passed only to the steps that use it ([reference.md §11](reference.md#11-secret-environments)).
- **A comment follows the line it describes**; it goes only with that line, and the pull request lists each one that went.
- **Read-only outside the branch.** Settings, secrets, rulesets and Apps change through Human Steps, never from this run. The one write outside the branch: an issue for a check already failing (step 1).

## Steps

### 1. Audit

```bash
bash <skill>/scripts/audit.sh OWNER/REPO <clone>
```

It scans the default branch whatever the clone has checked out, so its counts are the "before" of the pull request. Done when it exits 0 and you have its full output (exit 2 means GitHub was unreachable: fix that first). A section the token cannot read is reported as such; carry it to the pull request as is.

Then list the checks that fail on the default branch today (`gh run list -R OWNER/REPO --branch <default> --limit 10`). For each one, with the run that shows it:

- the cause is in a workflow file: fix it in this pull request, in its own commit (`ci: fix <check>, already failing on <default>`);
- otherwise: open a GitHub issue in the repository (label `ci`, the run linked) and keep its link for the pull request.

### 2. Fix the findings

Create branch `ci/zizmor-gate`, then work file by file, using the fix table in [reference.md](reference.md#fix-table). Pin actions with:

```bash
bash <skill>/scripts/pin-action.sh actions/checkout@v4   # → actions/checkout@<sha> # v4.4.0
```

Rerun after each batch:

```bash
bash <skill>/scripts/gate-scan.sh OWNER/REPO <clone>
```

Done when it exits 0, or every remaining finding is one a fix cannot remove without changing what the workflow does. Existing zizmor Exceptions and existing pins the scan does not flag stay as they are.

Then apply every item below that the audit shows for this repository; the Gate does not check them.

- **Secrets**: each job that reads a secret declares its Secret Environment; a secret in workflow- or job-level `env:` moves to the steps that use it; an existing Environment off the naming convention is renamed. Collect the `migrate_secrets.py` arguments for step 4 ([§11](reference.md#11-secret-environments)).
- **Job names**: required checks first. A job whose check the audit lists as "required check" takes that check's current name as `name:` (for a matrix, the name without the matrix values: `name: test` for `test (3.11)`). Every other job takes its key in sentence case (`build-docs` → `Build docs`), and the pull request lists the rename.
- **Permissions**: every grant carries a comment saying what it reads or writes (`contents: read # checkout`); a grant no step uses is removed.
- **Renovate**: the config extends `github>Giskard-AI/.github`, without the presets it already includes (`config:recommended`, `helpers:pinGitHubActionDigests`); other settings stay. Without a config, add `renovate.json`: `{"$schema": "https://docs.renovatebot.com/renovate-schema.json", "extends": ["github>Giskard-AI/.github"]}`.
- **Dependabot version updates** (`.github/dependabot.yml`): remove them when the audit shows the `renovate` App covering this repository; otherwise keep them and add the Human Step "install Renovate, then remove `dependabot.yml`".
- **The repository's own zizmor job**: keep it while the audit shows "Gate ruleset: not active yet" (it must pass too); once the Gate is active, delete it.
- **Push to a protected branch with `github.token`** (the audit lists it): the push fails today. Recommend reading the version from the tag when the only push is the version commit, a GitHub App with bypass otherwise ([§12](reference.md#12-releases-that-push-to-a-protected-branch)).
- **Registry login with a static secret**: switch to OIDC ([§9](reference.md#9-oidc-federation-for-container-registries)); the registry-side trust is a Human Step.
- **Privileged triggers** (`pull_request_target`, `workflow_run`, `issue_comment`): run the checklist of [§13](reference.md#13-privileged-triggers-checklist) on each such workflow before proposing any `dangerous-triggers` Exception.
- **Release tags**: a workflow that publishes on a pushed `v*` tag needs tag creation reserved by a ruleset; a Human Step unless the audit shows it.

### 3. Verify locally

```bash
python3 <skill>/scripts/workflow_contract.py <clone> --base origin/<default>
```

It prints the **Workflow Contract** of each changed workflow before and after (triggers and inputs, secrets, variables, permissions, Environments, outputs, `steps.<id>.outputs` references, actions, publishing commands) and runs `actionlint` on both sides. Done when all hold:

- `gate-scan.sh` exits 0 (counting step 4's proposed Exceptions, if any);
- every contract change is one you made on purpose, and `actionlint` reports nothing the default branch does not already report;
- `git diff` changes only `.github/`, `action.yml` files and the Renovate config, plus the files of a commit marked `(needs a decision)`.

### 4. Open a draft pull request

Commits: `ci: pass the zizmor Gate` for the workflows, `chore(renovate): extend the shared preset` for Renovate and Dependabot, each `ci: fix <check> ...` from step 1. Proposals that change behaviour come last, one commit each, marked `(needs a decision)`: the recommended Exceptions (`ci: propose zizmor exceptions (needs a decision)`) and a version read from the tag (`build: read the version from the release tag (needs a decision)`). Title `ci: pass the zizmor Gate`. Body sections, each omitted when empty:

1. **Fixed**: findings by rule, counts before → after, and the scan's first line (the giskard-actions ref) and summary line.
2. **Workflow Contract**: the script's table for each changed workflow, with one line of why per change: renamed checks, removed grants, replaced actions, comments that went with their line.
3. **Not run by CI**: each changed workflow the pull request's checks do not run (release, dispatch, schedule), and what to run after the merge to prove it still works.
4. **Renovate**: what the shared preset changes for this repository: schedule (Monday 00:00-06:00 Paris), 7-day wait before a new version (security fixes excepted), one pull request per package manager, lock file maintenance, Python never bumped.
5. **Decisions needed**: each `(needs a decision)` commit and each remaining finding, with file:line, why zizmor flags it, the §13 checklist result for privileged triggers, and your recommendation. The pull request stays a draft until each is decided.
6. **Checks already failing**: each one, with the fixing commit or the issue link.
7. **Human Steps**, in order, each one a command to run:
   1. Before merging: `python3 <skill>/scripts/migrate_secrets.py --repo OWNER/REPO --env <name>:<policy>:<SECRET>[,<SECRET>] ... [--delete <UNUSED_SECRET>]` (plan with `--dry-run`), then rerun the pull request's checks: green proves the migration.
   2. A personal access token, deploy key or App with more access than the job needs: `python3 <skill>/scripts/setup_github_app.py --dry-run ...` filled in (§8).
   3. Ruleset changes: required checks renamed, release tag creation reserved, an App bypass on an organization ruleset (org admin).
   4. Registry-side OIDC trust; Renovate installed on this repository.

   Org-wide audit findings (`SENSITIVE` Apps, organization ruleset bypass) belong to the repo-baseline dashboard; leave them out.

Done when the draft pull request is open and `gh pr checks <pr> --watch` shows every check passing except those listed under "Checks already failing".
