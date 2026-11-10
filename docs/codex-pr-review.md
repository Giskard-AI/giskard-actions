---
title: Codex PR review
nav_order: 3
---

# Codex PR review
{: .no_toc }

A reusable workflow that reviews a pull request with [Codex](https://github.com/openai/codex-action) against an Azure Responses-compatible endpoint. Reviewer passes are selected from the changed paths, each pass writes structured findings, and an aggregator turns them into one advisory review posted on the pull request. It never approves or blocks a pull request.

1. TOC
{:toc}

## Caller workflow

{% raw %}
```yaml
name: Codex PR review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled]
  issue_comment:
    types: [created]
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request number to review
        required: true
        type: string

permissions: {}

jobs:
  codex:
    if: >-
      (
        github.event_name == 'pull_request' &&
        contains(github.event.pull_request.labels.*.name, 'codex-review')
      ) ||
      github.event_name == 'workflow_dispatch' ||
      (
        github.event_name == 'issue_comment' &&
        github.event.issue.pull_request &&
        contains(fromJSON('["/review","/codex-assess"]'), github.event.comment.body) &&
        contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association)
      )
    concurrency:
      group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.event.issue.number || inputs.pr_number || github.run_id }}
      cancel-in-progress: ${{ github.event_name != 'issue_comment' }}
    permissions:
      contents: read
      issues: read
      pull-requests: write
    uses: Giskard-AI/giskard-actions/.github/workflows/codex-pr-review.yml@<full-commit-sha> # vX.Y.Z
    with:
      environment: azure-codex-review
    secrets:
      AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
```
{% endraw %}

What the caller owns:

- **Triggers and the comment gate.** Only start reviews for a label, a manual dispatch, or a `/review` or `/codex-assess` comment from an `OWNER`, `MEMBER` or `COLLABORATOR`. That `author_association` check is a cheap pre-filter: the workflow then calls `GET /repos/{owner}/{repo}/collaborators/{user}/permission` and only reviews when the commenter has `write`, `maintain` or `admin`. It also only reviews open, same-repository pull requests.
- **Concurrency.** Put the concurrency group on the calling job, not at workflow level. Every comment on the pull request starts a workflow run; with a workflow-level group, an unrelated comment would queue behind a `/review` and replace (cancel) it while pending. The reusable workflow does not declare one.
- **The permission ceiling.** The calling job's `permissions` cap every job in the reusable workflow, which needs `contents: read`, `issues: read` and `pull-requests: write`.
- **Secrets.** Pass `AZURE_AI_API_KEY` explicitly by name. Never use `secrets: inherit`. If the key exists only as an environment secret, it still works: the reviewer and aggregate jobs set `environment:` to your `environment` input, and an environment secret with the same name takes precedence over the passed value.
- **The environment.** Spell the `environment` input exactly: GitHub creates a missing environment on first use, so a typo silently runs with an empty environment and the review fails on the missing key or endpoint. If the environment restricts deployment branches and you trigger reviews from `pull_request` (for example the `codex-review` label), allow `refs/pull/*/merge`, the ref those runs execute on. Required reviewers on the environment would pause every reviewer job for approval.
- **Required checks.** Do not make the Codex jobs required status checks. A failed reviewer pass turns its job red so you can re-run it, but the review is advisory and must never block a merge.

## Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `environment` | string | (required) | Environment in **your** repository holding `AZURE_AI_API_KEY` and `AZURE_AI_RESPONSES_BASE`, e.g. `azure-codex-review` |
| `runner` | string | `ubuntu-slim` | Runner label for the prepare, reviewer and aggregate jobs |
| `timeout_minutes` | number | `15` | Timeout of each reviewer job and of the aggregate job |
| `max_parallel` | number | `4` | Reviewer jobs running at once in one review |
| `allow_self_hosted` | boolean | `false` | Allow self-hosted runners; see [Self-hosted runners](#self-hosted-runners) |
| `responses_api_base` | string | `''` | Azure Responses API base URL; empty uses `vars.AZURE_AI_RESPONSES_BASE` from your repository or environment |
| `normal_model` / `normal_effort` | string | `''` | Defaults for normal reviewer passes |
| `critical_model` / `critical_effort` | string | `''` | Defaults for critical reviewer passes |
| `aggregator_model` / `aggregator_effort` | string | `''` | Model and effort for the aggregator |
| `codex_version` | string | `0.164.0` | Codex CLI version |

Empty model and effort inputs fall back to the reviewer map `defaults`, then to built-in defaults (`gpt-5.6-luna` / `medium` for normal passes, `gpt-5.6-terra` / `high` for critical passes and the aggregator).

### Runners and timing

The default `ubuntu-slim` runner is verified to run the Codex sandbox with sudo dropped. GitHub caps `ubuntu-slim` jobs at 15 minutes, which is why `timeout_minutes` defaults to `15`; since each pass has its own job, the cap applies per pass, not to the whole review. For very large pull requests, pass `runner: ubuntu-latest` together with a larger `timeout_minutes`.

`max_parallel` caps one review. Several open pull requests reviewed at the same time each run up to `max_parallel` reviewer jobs against your Azure deployment, so lower it if you hit rate limits. The worst-case wall time is roughly the prepare job, plus `ceil(reviewers / max_parallel) × timeout_minutes`, plus the aggregate job and the post job.

Before Codex starts, each reviewer and aggregate job runs `codex sandbox -- true` and fails with a clear error if Codex cannot start its sandbox. On Ubuntu 24.04 runners such as `ubuntu-latest`, it first sets `kernel.apparmor_restrict_unprivileged_userns=0` so bubblewrap can create user namespaces. It only does this on GitHub-hosted runners, which are ephemeral.

### `responses_api_base`

Accepted forms (HTTPS on an Azure host):

- `https://<resource>.openai.azure.com/openai/v1`
- `https://<resource>.services.ai.azure.com/api/projects/<project>`
- any of the above ending in `/responses`

## Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `AZURE_AI_API_KEY` | yes | Azure key, passed only to `openai/codex-action` and the Key Leak Check in the reviewer and aggregate jobs |

## Outputs

| Output | Description |
| --- | --- |
| `should_run` | `true` when an open, same-repository pull request was resolved and reviewed |
| `pr_number` | Resolved pull request number |

## Jobs

1. **Prepare Codex review** (`prepare`) runs on `runner` with read-only GitHub permissions and no Azure key. It resolves the pull request, checks out your repository at the **default branch HEAD** into `trusted-config/` (the only source of the reviewer map and personas), collects the diff and discussion, selects the reviewer passes and stages each one's instructions. It uploads them as the `codex-review-input-<pr>` artifact (kept 1 day).
2. **Codex reviewer (`<name>`)** (`reviewer`) runs once per selected reviewer, at most `max_parallel` at a time, in your `environment`. Each job runs one Codex pass with sudo dropped, checks the output for the API key, and uploads `codex-reviewer-output-<pr>-<name>` (kept 1 day). A failed pass turns its job red; use **Re-run failed jobs** to retry just that reviewer. Skipped when no reviewer matches the changed paths.
3. **Codex review aggregator** (`aggregate`) runs in your `environment` once the reviewers finish, even if some failed. It keeps only the outputs this review expected, replaces every missing, stale or malformed one with a placeholder, lists them in the job summary, runs the aggregator pass with sudo dropped, sanitizes the result, checks it for the API key and uploads `codex-reviewer-outputs-<pr>` (kept 7 days). It does not run when the workflow is cancelled.
4. **Post advisory review** (`post`) runs on `ubuntu-slim` with `pull-requests: write` and no Azure key. It downloads the artifact and posts `final-review.md` as a `COMMENT` review.

## Reviewer personas

For each selected reviewer `<name>`, the prepare job looks for a persona in this order:

1. `<agents_dir>/<name>.md` in your repository (Cursor, Claude Code and Gemini CLI layout);
2. `<agents_dir>/<name>.agent.md` (GitHub Copilot layout);
3. a generic persona bundled under `.github/codex/overlays/` (`security-auditor`, `infra-reviewer`, `prompt-critic`);
4. otherwise a generic reviewer prompt; the final review notes that your repository has no persona for that reviewer.

`agents_dir` defaults to `.cursor/agents`, and `role_references` are read from `references_dir` (default `<agents_dir>/references`). Set both in `.github/giskard/codex-reviewer-map.json`, for example `".claude/agents"` or `".github/agents"`. YAML frontmatter is stripped; only the body reaches Codex, and fields such as `model` or `tools` are ignored. Symlinks, paths outside the repository and files over 30,000 characters fail the prepare job. See [Configuration](configuration.html#codex-reviewer-map).

Without a reviewer map, the workflow selects only the three bundled reviewers: `security-auditor` for workflows, actions, CI scripts, package files and security-sensitive paths; `infra-reviewer` for workflows, actions, package and infrastructure files; `prompt-critic` for prompts and agent files.

## What Codex sees

Codex gets the pull request patches, title and body (first 20,000 characters), unresolved review threads, issue comments and prior Codex reviews. The body, and comments, are included only when their author is an `OWNER`, `MEMBER` or `COLLABORATOR`, each labelled with that association; bot comments are dropped. Secret-looking values are redacted (GitHub expressions such as {% raw %}`${{ secrets.NAME }}`{% endraw %} are kept, since they are references, not values).

Before posting, the review body is sanitized: secret patterns are redacted, `@mentions` are defanged, images, raw HTML and bidirectional or zero-width characters are removed, and links are kept only for `github.com` and `sonarcloud.io` (any scheme case, protocol-relative `//host`, `www.` autolinks and HTML-entity-encoded URLs included). Links to runner workspace paths (`/home/runner/work/<repo>/<repo>/<path>:<line>`) become links to that file at the assessed head SHA; links to other absolute paths become inline code.

## Security model

The threat this workflow is built for is **prompt injection**: a diff, comment or pull request body that tries to make Codex leak the Azure key or post something harmful.

- **Codex never holds the key.** `openai/codex-action` keeps it in a local proxy process; Codex only talks to that proxy.
- **Sudo is dropped before Codex starts** (`safety-strategy: drop-sudo`), in every reviewer and aggregate job. Codex runs as the runner user but with the `nogroup` group, no supplementary groups (so no Docker socket), no capabilities and no way to regain privileges, so it cannot read the proxy's memory or environment. Because drop-sudo cannot run twice in one job, each pass has its own job.
- **Read-only sandbox without network.** Every command Codex runs is in a bubblewrap sandbox with the `:read-only` permission profile; web search is disabled.
- **Key Leak Check.** Before anything is uploaded, every output is searched for the key, raw and encoded (base64, base64url, hex, URL-encoded, reversed, with whitespace removed). On a match the job fails and nothing is uploaded or posted; rotate the key. A key split into small pieces would not be caught: isolation, not this check, is the real control.
- **Trusted configuration.** The reviewer map and personas come from the default branch HEAD, so neither the pull request nor a retargeted base branch can change which reviewers run or what they are told. Fork pull requests are never reviewed.
- **Least privilege per job.** Only prepare reads GitHub; only reviewer and aggregate jobs receive the key; only post can write, and it has no key.

What this does **not** protect against: **anyone with write access can read your CI secrets.** For `pull_request` runs from a branch of the same repository, GitHub runs the workflow file from the pull request, with secrets. A collaborator can edit any workflow on a branch to print the key, and no approval is needed. drop-sudo does not change that. Treat write access as access to the `environment`'s secrets, and use a dedicated Azure key with a spending limit.

### Public repositories

In a public repository, secrets are safe only while:

- **only organization members have write access** (no outside collaborators with `write`);
- **fork pull request workflows need approval for all external contributors** (repository settings, Actions, General). GitHub never passes repository or environment secrets to `pull_request` runs from forks, and this workflow refuses fork pull requests anyway;
- **secrets live in environments, not at repository level**, so only jobs that declare the environment get them.

Logs and artifacts of public repositories are visible to everyone, which is why every upload sits behind the Key Leak Check and intermediate artifacts are kept one day.

### Self-hosted runners

drop-sudo removes sudo, Docker access and group memberships on the runner for good. The prepare job therefore refuses self-hosted runners unless you set `allow_self_hosted: true`. Only do that with ephemeral runners that run one job per VM: on a persistent runner, later jobs would lose sudo, and runners sharing the `runner` user let Codex read other jobs' workspaces. The operator must also allow unprivileged user namespaces for bubblewrap.

The workflow references its own composites with the self-repository syntax (`uses: $/.github/actions/...`), which needs GitHub Actions runner 2.336.0 or later. GitHub-hosted runners already meet this.

## Migrating from v1

- The caller workflow does not change. Bump the pinned SHA and, optionally, set `max_parallel`.
- The check names change: one **Codex reviewer (`<name>`)** check per reviewer, plus **Prepare Codex review** and **Codex review aggregator**. Do not make them required.
- Personas and the reviewer map are now read from the **default branch HEAD**, not the pull request's base commit. A change to your reviewer map applies to open pull requests once it is merged.
- Without a reviewer map, only the three bundled reviewers run (v1 selected the reviewers of its first consumer).
- The `codex-pr-review` composite action is removed. The reusable workflow is the only supported interface; its composites (`codex-review-prepare`, `codex-review-pass`, `codex-post-review`) are internal.
- Self-hosted runners need `allow_self_hosted: true`; see above.

