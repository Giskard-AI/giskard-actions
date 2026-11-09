---
name: gha-hardening
disable-model-invocation: true
description: >-
  Harden one repository's GitHub Actions until it passes the Giskard zizmor
  Gate, and move its secrets into Environments. Use to fix zizmor findings,
  pin actions, scope secrets, or replace a token with a GitHub App.
---

# Harden a repository's GitHub Actions

The **Gate** is the org ruleset that runs `zizmor-scan.yml` from `Giskard-AI/giskard-actions`, with the auditor persona, on every pull request of a code repository. This skill brings one repository to a passing Gate in one draft pull request, and lists the **Human Steps** a person applies: settings changes before merging it, and the fix of each check already failing for a cause outside the workflows. Terms are defined in `GLOSSARY.md` of `giskard-actions`.

Threat models, fix samples and pitfalls: [reference.md](reference.md).

## Rules

- **One source of truth for the scan**: `scripts/gate-scan.sh` runs the Gate's own script at the Gate's commit. Judge only by its exit status: a pass there is a pass on the pull request.
- **Fix first.** A **zizmor Exception** (`# zizmor: ignore[<rule>] -- <reason>`) is a human decision, whatever the scan's message suggests: ask when a human is in the conversation; otherwise propose it as described in step 4.
- **Every secret lives in a Secret Environment**: one per kind of job, declared by every job that reads one of its secrets, and passed only to the steps that use it ([reference.md §11](reference.md#11-secret-environments)).
- **A comment follows the line it describes**; it goes only with that line, and the pull request lists each one that went.
- **Read-only outside the branch.** Settings, secrets, rulesets and Apps change through Human Steps, never from this run.

## Steps

### 1. Audit

```bash
bash <skill>/scripts/audit.sh OWNER/REPO <clone>
```

It scans the default branch whatever the clone has checked out, so its counts are the "before" of the pull request. Done when it exits 0 and you have its full output (exit 2 means GitHub was unreachable: fix that first). A section the token cannot read is reported as such; carry it to the pull request as is.

Then find what already fails on the default branch, in two audit sections:

- **Checks on <default> HEAD**: each check whose conclusion is not `success`, `neutral` or `skipped`, and each check whose latest pull request runs by others fail for a cause no pull request brought (a `STALE` green run on <default> hides it): both are already failing. Under each Actions check the audit prints its pull request runs by actor and the failed step and last error line of each latest failed run. A check that fails only on `dependabot[bot]` runs lacks its secrets there ([§11](reference.md#11-secret-environments)).
- **Workflows <default> does not run on push or pull request**: each one flagged `CHANGED SINCE ITS LAST RUN` or `NO RUN ON RECORD`, or whose last run failed. Read it for steps that cannot work today (a file it reads is missing or gitignored, a push the rules reject). When you can show why it fails, it is already failing, even though it is not a check.

For each one, when the cause is in a workflow file, fix it in this pull request, in its own commit (`ci: fix <check or workflow>, already failing on <default>`); otherwise list the cause under "Checks already failing" and add the Human Step that fixes it: replace the retired model in `<config file>`, put a new key into the Environment through the migration or `--rotate` ([Human Steps](reference.md#pull-request-body)). A fix that removes a step which cannot succeed today takes no `(needs a decision)`; the pull request says why the step could not succeed. Done when each failing check and each flagged workflow has a fix commit, a Human Step, or a line saying why it works.

### 2. Fix the findings

Create branch `ci/zizmor-gate`, then work file by file, using the fix table in [reference.md](reference.md#fix-table). Pin actions with:

```bash
bash <skill>/scripts/pin-action.sh actions/checkout@v4   # → actions/checkout@<sha> # v4.4.0
```

Rerun after each batch:

```bash
bash <skill>/scripts/gate-scan.sh OWNER/REPO <clone>
```

Done when it exits 0, or every remaining finding is one a fix cannot remove without changing what the workflow does. Existing zizmor Exceptions, pins and `concurrency:` blocks the scan does not flag stay as they are.

Then apply every item below that the audit shows for this repository; the Gate does not check them.

- **Secrets**: each job that reads a secret declares its Secret Environment; a secret in workflow- or job-level `env:` moves to the steps that use it; an existing Environment off the naming convention is renamed. Collect the `migrate_secrets.py` arguments for step 4 ([§11](reference.md#11-secret-environments)).
- **Job names**: required checks first. A job whose check the audit lists as "required check" (under a ruleset or classic branch protection) takes that check's current name as `name:` (for a matrix, the name without the matrix values: `name: test` for `test (3.11)`). Every other job takes its key in sentence case (`build-docs` → `Build docs`), and the pull request lists the rename.
- **Permissions**: every grant carries a comment saying what it reads or writes (`contents: read # checkout`); a grant no step uses is removed.
- **Renovate**: the config extends `github>Giskard-AI/.github`, drops what that shared preset already sets (the audit lists the overlap) and keeps the repository's own rules ([§14](reference.md#14-renovate)). Without a config, add `renovate.json`: `{"$schema": "https://docs.renovatebot.com/renovate-schema.json", "extends": ["github>Giskard-AI/.github"]}`.
- **Dependencies** ([§15](reference.md#15-dependency-lock-and-cooldown)): each project directory of the audit's "Dependencies" section managed by uv, pnpm or npm gets a committed lock (brought up to date when the audit says `lock out of date`), the one-week **Dependency cooldown** and a locked CI install; each Python project the audit marks `convert to uv` moves to uv when every reader of its old files can follow; each install outside the lock and each other manager is reported; an open vulnerability pull request the audit flags `YOUNGER THAN 7 DAYS` needs the security exemption.
- **Dependabot version updates** (`.github/dependabot.yml`): remove them when the audit shows the `renovate` App covering this repository; otherwise keep them and add the Human Step "install Renovate, then remove `dependabot.yml`".
- **The repository's own zizmor job** (the audit's "Own zizmor job on <default>" line): keep it while the audit shows "Gate ruleset: not active yet" (it must pass too); once the Gate is active, delete it.
- **Push to a protected branch with `github.token`** (the audit lists it): the push fails today. Run the consumers check, then recommend **Tag version** when the version commit is the only push, **Release App** otherwise ([§12](reference.md#12-releases-that-push-to-a-protected-branch)).
- **Registry login with a static secret**: switch to OIDC ([§9](reference.md#9-oidc-federation-for-container-registries)); the registry-side trust is a Human Step.
- **Privileged triggers** (`pull_request_target`, `workflow_run`, `issue_comment`): run the checklist of [§13](reference.md#13-privileged-triggers-checklist) on each such workflow before proposing any `dangerous-triggers` Exception.
- **Release tags**: a workflow that publishes on a pushed `v*` tag needs tag creation reserved by a ruleset; a Human Step unless the audit shows it.

### 3. Verify locally

```bash
uv run <skill>/scripts/workflow_contract.py <clone> --base origin/<default>
```

It prints the **Workflow Contract** of each changed workflow before and after (triggers and inputs, secrets, variables, permissions, Environments, outputs, `steps.<id>.outputs` references, actions, publishing commands), flags references the new version breaks, and runs `actionlint` on both sides. Done when all hold:

- `gate-scan.sh` exits 0 (counting step 4's proposed Exceptions, if any);
- `workflow_contract.py` exits 0 (no broken reference, no new `actionlint` error), and every contract change is one you made on purpose;
- `git diff` changes only `.github/`, `action.yml` files, the Renovate config, and the lock files, `.gitignore`, cooldown settings and CI install commands of the dependency commit, plus the files of the conversion commit and of a commit marked `(needs a decision)`;
- for the dependency commit, the conversion commit and each decision commit that changes files outside `.github/`, the commands of the CI jobs the pull request triggers pass locally, and so do the checks of each conversion ([§15](reference.md#convert-a-python-project-to-uv)); the pull request reports them in that commit's section.

### 4. Open a draft pull request

Commits, in this order: each `ci: fix ...` from step 1; `ci: pass the zizmor Gate` for the workflows; `chore(renovate): extend the shared preset` for Renovate and Dependabot; `build: lock dependencies with a one-week cooldown`; `build: move <dirs> to uv`; then the proposals that change behaviour, one commit each, marked `(needs a decision)`: a relock that moves versions (`build: relock <dir> (needs a decision)`), the recommended Exceptions (`ci: propose zizmor exceptions (needs a decision)`) and Tag version (`build: read the version from the release tag (needs a decision)`). The Gate commit keeps the current behaviour, including code a later decision commit removes: the Gate passes at every commit.

Title `ci: pass the zizmor Gate`. Write the body section by section as [reference.md](reference.md#pull-request-body) describes them: **Fixed**, **Workflow Contract**, **Not run by CI**, **Renovate**, **Dependencies**, **Decisions needed**, **Checks already failing**, **Human Steps** (kept when empty, with one line saying so). The pull request stays a draft until each decision is made.

Done when the draft pull request is open and `gh pr checks <pr> --watch` shows every check passing except those listed under "Checks already failing".
