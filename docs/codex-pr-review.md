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
      environment: ci
    secrets:
      AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}
```
{% endraw %}

What the caller owns:

- **Triggers and the comment gate.** Only start reviews for a label, a manual dispatch, or a `/review` or `/codex-assess` comment from an `OWNER`, `MEMBER` or `COLLABORATOR`. That `author_association` check is a cheap pre-filter: the workflow then calls `GET /repos/{owner}/{repo}/collaborators/{user}/permission` and only reviews when the commenter has `write`, `maintain` or `admin`. It also only reviews open, same-repository pull requests.
- **Concurrency.** Put the concurrency group on the calling job, not at workflow level. Every comment on the pull request starts a workflow run; with a workflow-level group, an unrelated comment would queue behind a `/review` and replace (cancel) it while pending. The reusable workflow does not declare one.
- **The permission ceiling.** The calling job's `permissions` cap every job in the reusable workflow, which needs `contents: read`, `issues: read` and `pull-requests: write`.
- **Secrets.** Pass `AZURE_AI_API_KEY` explicitly by name. Never use `secrets: inherit`. If the key exists only as an environment secret, it still works: the review job sets `environment:` to your `environment` input, and an environment secret with the same name takes precedence over the passed value.
- **The environment.** Spell the `environment` input exactly: GitHub creates a missing environment on first use, so a typo silently runs with an empty environment and the review fails on the missing key or endpoint. If the environment restricts deployment branches and you trigger reviews from `pull_request` (for example the `codex-review` label), allow `refs/pull/*/merge`, the ref those runs execute on.

## Inputs

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `environment` | string | (required) | Environment in **your** repository holding `AZURE_AI_API_KEY` and `AZURE_AI_RESPONSES_BASE`, e.g. `ci` |
| `runner` | string | `ubuntu-slim` | Runner label for the review job |
| `timeout_minutes` | number | `15` | Review job timeout |
| `responses_api_base` | string | `''` | Azure Responses API base URL; empty uses `vars.AZURE_AI_RESPONSES_BASE` from your repository or environment |
| `normal_model` / `normal_effort` | string | `''` | Defaults for normal reviewer passes |
| `critical_model` / `critical_effort` | string | `''` | Defaults for critical reviewer passes |
| `aggregator_model` / `aggregator_effort` | string | `''` | Model and effort for the aggregator |
| `codex_version` | string | `0.164.0` | Codex CLI version |

Empty model and effort inputs fall back to the reviewer map `defaults`, then to built-in defaults (`gpt-5.6-luna` / `medium` for normal passes, `gpt-5.6-terra` / `high` for critical passes and the aggregator).

### Runners

The default `ubuntu-slim` runner is verified to run the Codex sandbox, and a typical review finishes in a few minutes. GitHub caps `ubuntu-slim` jobs at 15 minutes, which is why `timeout_minutes` defaults to `15`. For large pull requests that select many reviewer passes, pass `runner: ubuntu-latest` together with a larger `timeout_minutes`, e.g. `60`.

Before the first pass, the review job runs `codex sandbox -- true` and fails with a clear error if Codex cannot start its sandbox. On Ubuntu 24.04 runners such as `ubuntu-latest`, it first sets `kernel.apparmor_restrict_unprivileged_userns=0` so bubblewrap can create user namespaces. It only does this on GitHub-hosted runners, which are ephemeral; on a self-hosted runner, the operator must allow unprivileged user namespaces beforehand.

### `responses_api_base`

Accepted forms (HTTPS on an Azure host):

- `https://<resource>.openai.azure.com/openai/v1`
- `https://<resource>.services.ai.azure.com/api/projects/<project>`
- any of the above ending in `/responses`

## Secrets

| Secret | Required | Description |
| --- | --- | --- |
| `AZURE_AI_API_KEY` | yes | Azure key, passed only to `openai/codex-action` and the Key Leak Check |

## Outputs

| Output | Description |
| --- | --- |
| `should_run` | `true` when an open, same-repository pull request was resolved and reviewed |
| `pr_number` | Resolved pull request number |

## Jobs

1. **`review`** runs on `runner` in your `environment` with read-only permissions. It resolves the pull request, preflights the Codex sandbox, creates the Codex User, checks out your repository at the pull request **base SHA** into `trusted-config/` (the only source of the reviewer map and personas), collects the diff and discussion, runs up to 12 reviewer passes plus the aggregator, sanitizes the result, runs the Key Leak Check and uploads the `codex-reviewer-outputs` artifact.
2. **`post`** runs on `ubuntu-slim` with `pull-requests: write` and no Azure credentials. It downloads the artifact and posts `final-review.md` as a `COMMENT` review.

## Reviewer personas

For each selected reviewer `<name>`, the workflow loads `.cursor/agents/<name>.md` from the base SHA plus any `role_references` from `.cursor/agents/references/`. If your repository has no such persona, it falls back to a generic one bundled under `.github/codex/overlays/` (`security-auditor`, `infra-reviewer`, `prompt-critic`); any other reviewer without a persona fails its pass. Configure selection in `.github/giskard/codex-reviewer-map.json`; see [Configuration](configuration.html#codex-reviewer-map).

## What Codex sees

Codex gets the pull request patches, title and body, unresolved review threads, issue comments and prior Codex reviews. Only comments whose author is an `OWNER`, `MEMBER` or `COLLABORATOR` are included, each labelled with that association; bot comments are dropped. Secret-looking values are redacted (GitHub expressions such as {% raw %}`${{ secrets.NAME }}`{% endraw %} are kept, since they are references, not values).

Before posting, the review body is sanitized: secret patterns are redacted, `@mentions` are defanged, images, raw HTML and bidirectional or zero-width characters are removed, and links are kept only for `github.com` and `sonarcloud.io` (any scheme case, protocol-relative `//host`, `www.` autolinks and HTML-entity-encoded URLs included).

## Security model

Codex reads text the pull request author controls (the diff and the discussion), so a review must assume that text contains a prompt injection aimed at `AZURE_AI_API_KEY`. That holds for every trigger: the `/review` comment, the `codex-review` label and manual runs start the same job. The review job never checks out or runs the pull request's code.

- **The key stays in codex-action's proxy.** `openai/codex-action` starts a local Responses API proxy that receives the key on stdin and keeps it out of its environment. Codex only gets a `127.0.0.1` URL.
- **Codex runs as the Codex User.** Every reviewer pass and the aggregator use `safety-strategy: unprivileged-user` with `codex-user: codex`, an account the review job creates without `sudo`. The proxy runs under the runner account, so Codex can't read its memory even after escaping its sandbox. With the previous `read-only` strategy, the runner's passwordless `sudo` made that memory readable ([why](https://github.com/openai/codex-action/blob/main/docs/security.md#protecting-your-openai_api_key)).
- **Read-only, no network.** Each pass uses `permission-profile: ":read-only"`: Codex's commands run in a bubblewrap sandbox that can't write files or open network connections.
- **Key Leak Check.** After the aggregator and before the artifact upload, the job fails if the key's value appears anywhere under `assessment/`. Nothing is then uploaded or posted; the error says to rotate `AZURE_AI_API_KEY` and never prints the value. It only catches the key in plain text: the Codex User is the real protection.
- **Why not `drop-sudo`.** codex-action's default removes `sudo` from the runner for the rest of the job, so a second Codex run in the same job fails. The review runs up to 13 passes in one job; `drop-sudo` would need one job per pass.
- **Comment trigger.** `/review` only runs for `OWNER`, `MEMBER` and `COLLABORATOR` comments. zizmor flags the `issue_comment` trigger as `dangerous-triggers`; callers keep it with an inline ignore that states both the gate and the protections above.

## Residual risks

- **Encoded key in output.** The Key Leak Check only matches the key in plain text. Keep using a dedicated Azure key with a spending limit, scoped to this workflow.
- **Advisory output.** Model output is sanitized but not verified. Treat findings as suggestions.

## Known limitations

- All reviewer passes run sequentially in one job.
- Self-hosted runners need passwordless `sudo` for the runner account (to create the Codex User) and `useradd`.

## Runner requirements

The workflow references its own composites with the self-repository syntax (`uses: $/.github/actions/codex-pr-review`), which needs GitHub Actions runner 2.336.0 or later. GitHub-hosted runners already meet this; update self-hosted runners before upgrading.

## Using the composites directly

The `codex-pr-review` and `codex-post-review` composite actions under `.github/actions/` remain available if you need a different job layout. The reusable workflow is the supported interface; if you call the composites yourself, keep the read and write jobs separate as above.
