import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

KEY_WORKFLOWS = ()
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
REVIEWER_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_REVIEWERS = 12
DEFAULT_AZURE_HOST_SUFFIXES = (
    ".openai.azure.com",
    ".cognitiveservices.azure.com",
    ".services.ai.azure.com",
)
TERMINAL_STATUS = "completed"
LOG_LIMIT = 20_000
ANNOTATION_LIMIT = 50
DIFF_PATCH_LIMIT = 120_000
FAILED_LOG_RUN_LIMIT = 12
MAX_REVIEW_BODY_CHARS = 60_000
ALLOWED_LINK_HOSTS = {"github.com", "www.github.com", "sonarcloud.io", "www.sonarcloud.io"}
# gpt-5.6-* catalog entries send ChatGPT-backend Responses Lite / collaboration
# tools that Azure rejects. See openai/codex#31882.
AZURE_INCOMPATIBLE_MODEL_SLUGS = ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra")
REQUIRED_AZURE_CATALOG_SLUGS = ("gpt-5.6-luna", "gpt-5.6-terra")
VENDORED_AZURE_MODEL_CATALOG = (
    Path(__file__).resolve().parents[2] / ".github/codex/azure-model-catalog.json"
)


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def load_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def append_github_output(path: str | Path, values: dict[str, str]) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            sanitized = value.replace("\r", "").replace("\n", "")
            handle.write(f"{key}={sanitized}\n")


def run_text(command: list[str], *, check: bool = True, stdin: str | None = None) -> str:
    completed = subprocess.run(
        command,
        check=check,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def gh_json(args: list[str], *, check: bool = True) -> Any:
    output = run_text(["gh", *args], check=check)
    if not output.strip():
        return None
    return json.loads(output)


def gh_text(args: list[str], *, check: bool = True) -> str:
    command = ["gh", *args]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    if completed.returncode != 0:
        return completed.stdout + completed.stderr
    return completed.stdout


def repo_from_env() -> GitHubRepo:
    repository = os.environ["GITHUB_REPOSITORY"]
    owner, name = repository.split("/", 1)
    return GitHubRepo(owner=owner, name=name)


def is_ignored_author(author: dict[str, Any] | None) -> bool:
    if not author:
        return True
    login = str(author.get("login") or "").lower()
    author_type = str(author.get("type") or author.get("__typename") or "").lower()
    return (
        author_type == "bot"
        or login.endswith("[bot]")
        or "gemini" in login
        or login == "github-actions"
    )


def is_trusted_comment_event(
    event: dict[str, Any], commands: str | set[str] = "/codex-assess"
) -> bool:
    if event.get("action") != "created":
        return False
    issue = event.get("issue") or {}
    if "pull_request" not in issue:
        return False
    comment = event.get("comment") or {}
    allowed_commands = {commands} if isinstance(commands, str) else commands
    if str(comment.get("body") or "").strip() not in allowed_commands:
        return False
    return str(comment.get("author_association") or "") in TRUSTED_ASSOCIATIONS


def resolve_pr_number(event: dict[str, Any], repo: GitHubRepo) -> int | None:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs") or {}
        pr_number = inputs.get("pr_number")
        return int(pr_number) if pr_number else None

    if event_name == "pull_request":
        pull_request = event.get("pull_request") or {}
        return int(pull_request["number"]) if pull_request.get("number") else None

    if event_name == "issue_comment":
        commands = {
            item.strip()
            for item in os.environ.get(
                "CODEX_ASSESS_COMMENT_COMMANDS",
                os.environ.get("CODEX_ASSESS_COMMENT_COMMAND", "/codex-assess"),
            ).split(",")
            if item.strip()
        }
        if not is_trusted_comment_event(event, commands):
            return None
        return int(event["issue"]["number"])

    if event_name == "workflow_run":
        if os.environ.get("CODEX_ASSESS_ALLOW_WORKFLOW_RUN", "false") != "true":
            return None
        workflow_run = event.get("workflow_run") or {}
        if workflow_run.get("event") != "pull_request":
            return None
        pull_requests = workflow_run.get("pull_requests") or []
        if pull_requests:
            return int(pull_requests[0]["number"])
        head_sha = workflow_run.get("head_sha")
        if not head_sha:
            return None
        prs = gh_json(
            [
                "api",
                f"repos/{repo.full_name}/commits/{head_sha}/pulls",
                "-H",
                "Accept: application/vnd.github+json",
            ],
            check=False,
        )
        if isinstance(prs, list) and prs:
            return int(prs[0]["number"])
    return None


def collect_paginated(endpoint: str, *, list_key: str | None = None) -> list[dict[str, Any]]:
    data = gh_json(["api", "--paginate", "--slurp", endpoint])
    if data is None:
        return []
    if isinstance(data, list):
        if data and all(isinstance(item, list) for item in data):
            flattened: list[dict[str, Any]] = []
            for page in data:
                flattened.extend(page)
            return flattened
        if data and all(isinstance(item, dict) for item in data):
            flattened = []
            for page in data:
                if list_key is None:
                    raise ValueError("list_key is required for object-shaped paginated data")
                value = page.get(list_key)
                if isinstance(value, list):
                    flattened.extend(value)
            return flattened or data
        return data
    if isinstance(data, dict):
        if list_key is not None and isinstance(data.get(list_key), list):
            return data[list_key]
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def get_pr(repo: GitHubRepo, pr_number: int) -> dict[str, Any]:
    return gh_json(["api", f"repos/{repo.full_name}/pulls/{pr_number}"])


def is_same_repo_pr(pr: dict[str, Any]) -> bool:
    return ((pr.get("head") or {}).get("repo") or {}).get("full_name") == (
        (pr.get("base") or {}).get("repo") or {}
    ).get("full_name")


def collect_pr_files(repo: GitHubRepo, pr_number: int) -> list[dict[str, Any]]:
    files = collect_paginated(f"repos/{repo.full_name}/pulls/{pr_number}/files")
    return [
        {
            "filename": item.get("filename"),
            "status": item.get("status"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "changes": item.get("changes"),
            "patch": trim_text(
                redact_secret_references(str(item.get("patch") or "")),
                DIFF_PATCH_LIMIT,
            ),
        }
        for item in files
    ]


def collect_issue_comments(repo: GitHubRepo, pr_number: int) -> list[dict[str, Any]]:
    comments = collect_paginated(f"repos/{repo.full_name}/issues/{pr_number}/comments")
    result = []
    for comment in comments:
        if is_ignored_author(comment.get("user")):
            continue
        body = str(comment.get("body") or "")
        if body.strip() in {"/review", "/codex-assess", "/codex-ci-assess"}:
            continue
        result.append(
            {
                "id": comment.get("id"),
                "author": (comment.get("user") or {}).get("login"),
                "created_at": comment.get("created_at"),
                "body": redact_secret_references(body),
                "url": comment.get("html_url"),
            }
        )
    return result


def collect_prior_codex_reviews(repo: GitHubRepo, pr_number: int) -> list[dict[str, Any]]:
    reviews = collect_paginated(f"repos/{repo.full_name}/pulls/{pr_number}/reviews")
    result = []
    for review in reviews:
        body = str(review.get("body") or "")
        if not body.startswith("## Codex advisory code review"):
            continue
        author = review.get("user") or {}
        login = str(author.get("login") or "")
        if login not in {"github-actions", "github-actions[bot]"}:
            continue
        result.append(
            {
                "id": review.get("id"),
                "author": login,
                "submitted_at": review.get("submitted_at"),
                "commit_id": review.get("commit_id"),
                "state": review.get("state"),
                "body": trim_text(redact_secret_references(body), 20_000),
                "url": review.get("html_url"),
            }
        )
    return result


def collect_review_threads(repo: GitHubRepo, pr_number: int) -> list[dict[str, Any]]:
    query = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        nodes {
          id
          isResolved
          path
          line
          startLine
          comments(first: 50) {
            totalCount
            nodes {
              id
              body
              createdAt
              url
              author {
                login
                __typename
              }
            }
            pageInfo {
              hasNextPage
            }
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""
    cursor = None
    threads: list[dict[str, Any]] = []
    while True:
        graphql_args = [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={repo.owner}",
            "-f",
            f"name={repo.name}",
            "-F",
            f"number={pr_number}",
        ]
        if cursor:
            graphql_args.extend(["-f", f"cursor={cursor}"])
        response = gh_json(graphql_args)
        thread_page = (
            ((response or {}).get("data") or {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
        )
        for thread in thread_page.get("nodes") or []:
            if thread.get("isResolved"):
                continue
            human_comments = []
            for comment in (thread.get("comments") or {}).get("nodes") or []:
                author = comment.get("author") or {}
                if is_ignored_author(author):
                    continue
                human_comments.append(
                    {
                        "id": comment.get("id"),
                        "author": author.get("login"),
                        "created_at": comment.get("createdAt"),
                        "body": redact_secret_references(str(comment.get("body") or "")),
                        "url": comment.get("url"),
                    }
                )
            if human_comments:
                threads.append(
                    {
                        "id": thread.get("id"),
                        "path": thread.get("path"),
                        "line": thread.get("line"),
                        "start_line": thread.get("startLine"),
                        "comments_truncated": bool(
                            ((thread.get("comments") or {}).get("pageInfo") or {}).get(
                                "hasNextPage"
                            )
                        ),
                        "comments": human_comments,
                    }
                )
        page_info = thread_page.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    return threads


def workflow_runs_for_sha(repo: GitHubRepo, head_sha: str) -> list[dict[str, Any]]:
    runs = collect_paginated(
        f"repos/{repo.full_name}/actions/runs?head_sha={quote(head_sha)}&per_page=100",
        list_key="workflow_runs",
    )
    result = []
    for run in sorted(runs, key=lambda item: str(item.get("created_at") or ""), reverse=True):
        result.append(
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "event": run.get("event"),
                "html_url": run.get("html_url"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
            }
        )
    return result


def azure_host_allowlist() -> tuple[str, ...]:
    raw = os.environ.get("CODEX_AZURE_HOST_ALLOWLIST", "").strip()
    if raw:
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return DEFAULT_AZURE_HOST_SUFFIXES


def host_is_allowed_azure(hostname: str, suffixes: tuple[str, ...]) -> bool:
    host = hostname.lower().rstrip(".")
    for suffix in suffixes:
        token = suffix.lower().lstrip(".")
        if host == token or host.endswith(f".{token}"):
            return True
    return False


def load_ci_config(path: str | Path | None = None) -> dict[str, Any]:
    candidate = path or os.environ.get("CODEX_CI_CONFIG", "").strip() or None
    if not candidate:
        return {"key_workflows": [], "recommendations": []}
    resolved = Path(candidate)
    if not resolved.is_file():
        return {"key_workflows": [], "recommendations": []}
    payload = load_json(resolved)
    if not isinstance(payload, dict):
        raise ValueError("ci-config must be a JSON object")
    key_workflows = payload.get("key_workflows") or []
    recommendations = payload.get("recommendations") or []
    if not isinstance(key_workflows, list) or not isinstance(recommendations, list):
        raise ValueError("ci-config key_workflows and recommendations must be arrays")
    return {
        "key_workflows": [str(item) for item in key_workflows],
        "recommendations": [item for item in recommendations if isinstance(item, dict)],
    }


def latest_workflow_runs(
    repo: GitHubRepo, head_sha: str, workflow_names: tuple[str, ...] = KEY_WORKFLOWS
) -> dict[str, dict[str, Any]]:
    runs = workflow_runs_for_sha(repo, head_sha)
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run.get("name")
        if name not in workflow_names or name in latest:
            continue
        latest[name] = {
            "id": run.get("id"),
            "name": name,
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "html_url": run.get("html_url"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
        }
    for name in workflow_names:
        latest.setdefault(name, {"name": name, "status": "not_run", "conclusion": "skipped"})
    return latest


def collect_check_runs(repo: GitHubRepo, head_sha: str) -> list[dict[str, Any]]:
    checks = collect_paginated(
        f"repos/{repo.full_name}/commits/{head_sha}/check-runs?per_page=100",
        list_key="check_runs",
    )
    result = []
    for check in checks:
        annotations = []
        if check.get("conclusion") not in {None, "success", "skipped"}:
            annotations = collect_check_annotations(repo, int(check["id"]))
        result.append(
            {
                "id": check.get("id"),
                "name": check.get("name"),
                "status": check.get("status"),
                "conclusion": check.get("conclusion"),
                "details_url": check.get("details_url"),
                "html_url": check.get("html_url"),
                "annotations": annotations,
            }
        )
    return result


def collect_check_annotations(repo: GitHubRepo, check_run_id: int) -> list[dict[str, Any]]:
    annotations = collect_paginated(
        f"repos/{repo.full_name}/check-runs/{check_run_id}/annotations?per_page=100"
    )
    result = []
    for annotation in annotations[:ANNOTATION_LIMIT]:
        result.append(
            {
                "path": annotation.get("path"),
                "start_line": annotation.get("start_line"),
                "end_line": annotation.get("end_line"),
                "annotation_level": annotation.get("annotation_level"),
                "message": trim_text(
                    redact_secret_references(str(annotation.get("message") or "")),
                    2_000,
                ),
            }
        )
    return result


def collect_failed_logs(
    repo: GitHubRepo, workflow_runs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    logs = []
    for run in workflow_runs:
        if len(logs) >= FAILED_LOG_RUN_LIMIT:
            break
        conclusion = run.get("conclusion")
        run_id = run.get("id")
        if not run_id or conclusion in {"success", "skipped", None}:
            continue
        text = gh_text(
            ["run", "view", str(run_id), "--repo", repo.full_name, "--log-failed"],
            check=False,
        )
        logs.append(
            {
                "workflow": run.get("name"),
                "run_id": run_id,
                "conclusion": conclusion,
                "log_excerpt": trim_text(redact_secret_references(text), LOG_LIMIT),
            }
        )
    return logs


def collect_sonar(repo: GitHubRepo, pr_number: int) -> dict[str, Any] | None:
    token = os.environ.get("SONAR_QUBE_TOKEN")
    if not token:
        return None
    project_key = f"{repo.owner}_{repo.name}"
    auth = base64.b64encode(f"{token}:".encode()).decode()
    url = (
        "https://sonarcloud.io/api/issues/search"
        f"?componentKeys={quote(project_key)}"
        f"&pullRequest={pr_number}&resolved=false&sinceLeakPeriod=true&ps=50"
    )
    request = Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError):
        return {"error": "sonar_fetch_failed"}
    return {
        "total": payload.get("total", 0),
        "issues": [
            {
                "type": issue.get("type"),
                "severity": issue.get("severity"),
                "component": str(issue.get("component") or "").split(":", 1)[-1],
                "line": issue.get("line"),
                "rule": issue.get("rule"),
                "message": redact_secret_references(str(issue.get("message") or "")),
            }
            for issue in payload.get("issues", [])
        ],
    }


def trim_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def pr_metadata(pr_number: int, pr: dict[str, Any], repo: GitHubRepo) -> dict[str, Any]:
    return {
        "repository": repo.full_name,
        "pr": {
            "number": pr_number,
            "title": pr.get("title"),
            "state": pr.get("state"),
            "draft": pr.get("draft"),
            "base_ref": pr["base"]["ref"],
            "base_sha": pr["base"]["sha"],
            "head_ref": pr["head"]["ref"],
            "head_sha": pr["head"]["sha"],
            "url": pr.get("html_url"),
            "author": (pr.get("user") or {}).get("login"),
        },
    }


def collect_review_context(pr_number: int) -> dict[str, Any]:
    repo = repo_from_env()
    pr = get_pr(repo, pr_number)
    return {
        **pr_metadata(pr_number, pr, repo),
        "files": collect_pr_files(repo, pr_number),
        "comments": collect_issue_comments(repo, pr_number),
        "review_threads": collect_review_threads(repo, pr_number),
        "prior_codex_reviews": collect_prior_codex_reviews(repo, pr_number),
    }


def collect_ci_context(pr_number: int) -> dict[str, Any]:
    repo = repo_from_env()
    pr = get_pr(repo, pr_number)
    head_sha = pr["head"]["sha"]
    workflow_runs = workflow_runs_for_sha(repo, head_sha)
    ci_config = load_ci_config()
    key_names = tuple(ci_config["key_workflows"])
    key_workflow_runs = latest_workflow_runs(repo, head_sha, workflow_names=key_names)
    return {
        **pr_metadata(pr_number, pr, repo),
        "files": [
            {"filename": item.get("filename"), "status": item.get("status")}
            for item in collect_pr_files(repo, pr_number)
        ],
        "key_workflow_runs": key_workflow_runs,
        "workflow_runs": workflow_runs,
        "check_runs": collect_check_runs(repo, head_sha),
        "failed_logs": collect_failed_logs(repo, workflow_runs),
        "sonar": collect_sonar(repo, pr_number),
        "ci_config": ci_config,
    }


def is_non_green_status(status: str | None, conclusion: str | None) -> bool:
    if status not in {TERMINAL_STATUS, "not_run"}:
        return True
    return conclusion not in {None, "success", "skipped"}


def has_terminal_or_skipped_key_workflows(workflow_runs: dict[str, dict[str, Any]]) -> bool:
    if not workflow_runs:
        return True
    for run in workflow_runs.values():
        status = run.get("status")
        if status != TERMINAL_STATUS:
            return False
    return True


def classify_ci_evidence(name: str, text: str, conclusion: str | None) -> str:
    haystack = f"{name}\n{text}".lower()
    if conclusion == "cancelled" or "cancelled" in haystack or "canceled" in haystack:
        return "cancelled"
    if conclusion == "timed_out" or "timed out" in haystack or "timeout" in haystack:
        return "timeout"
    if "playwright" in haystack or "e2e" in haystack or "browser" in haystack:
        return "e2e_failure"
    if "pytest" in haystack or "assertionerror" in haystack or "test failed" in haystack:
        return "test_failure"
    if "eslint" in haystack or "ruff" in haystack or "pylint" in haystack or "lint" in haystack:
        return "lint_failure"
    if "pnpm" in haystack or "npm" in haystack or "build failed" in haystack:
        return "build_failure"
    if "docker" in haystack or "network" in haystack or "no space left" in haystack:
        return "infra_failure"
    return "unknown"


def triage_ci(context: dict[str, Any]) -> dict[str, Any]:
    key_workflow_runs = context.get("key_workflow_runs") or {}
    workflow_runs = context.get("workflow_runs") or []
    check_runs = context.get("check_runs") or []
    failed_logs = context.get("failed_logs") or []
    key_workflows = []
    for name, run in key_workflow_runs.items():
        key_workflows.append(
            {
                "name": name,
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "url": run.get("html_url"),
                "needs_attention": is_non_green_status(run.get("status"), run.get("conclusion")),
            }
        )

    workflows_needing_attention = []
    for run in workflow_runs:
        status = run.get("status")
        conclusion = run.get("conclusion")
        if not is_non_green_status(status, conclusion):
            continue
        workflows_needing_attention.append(
            {
                "name": run.get("name"),
                "status": status,
                "conclusion": conclusion,
                "url": run.get("html_url"),
                "classification": classify_ci_evidence(str(run.get("name") or ""), "", conclusion),
            }
        )

    checks_needing_attention = []
    annotation_count = 0
    for check in check_runs:
        status = check.get("status")
        conclusion = check.get("conclusion")
        annotations = check.get("annotations") or []
        annotation_count += len(annotations)
        if not is_non_green_status(status, conclusion):
            continue
        checks_needing_attention.append(
            {
                "name": check.get("name"),
                "status": status,
                "conclusion": conclusion,
                "url": check.get("html_url") or check.get("details_url"),
                "classification": classify_ci_evidence(
                    str(check.get("name") or ""),
                    "\n".join(str(item.get("message") or "") for item in annotations),
                    conclusion,
                ),
                "annotations": annotations,
            }
        )

    log_summaries = []
    for log in failed_logs:
        text = str(log.get("log_excerpt") or "")
        log_summaries.append(
            {
                "workflow": log.get("workflow"),
                "run_id": log.get("run_id"),
                "conclusion": log.get("conclusion"),
                "classification": classify_ci_evidence(
                    str(log.get("workflow") or ""), text, log.get("conclusion")
                ),
                "excerpt": trim_text(text, 4_000),
            }
        )

    recommendations = recommend_additional_ci(
        context,
        key_workflow_runs,
        workflows_needing_attention,
        ci_config=context.get("ci_config"),
    )
    ready = has_terminal_or_skipped_key_workflows(key_workflow_runs)
    needs_attention = bool(
        [item for item in key_workflows if item["needs_attention"]]
        or workflows_needing_attention
        or checks_needing_attention
        or ((context.get("sonar") or {}).get("total") or 0)
        or recommendations
        or not ready
    )
    return {
        "pr": context.get("pr"),
        "ready": ready,
        "needs_ai_diagnosis": needs_attention,
        "summary": {
            "key_workflows_total": len(key_workflows),
            "key_workflows_needing_attention": len(
                [item for item in key_workflows if item["needs_attention"]]
            ),
            "workflows_total": len(workflow_runs),
            "workflows_needing_attention": len(workflows_needing_attention),
            "checks_total": len(check_runs),
            "checks_needing_attention": len(checks_needing_attention),
            "annotations_total": annotation_count,
            "failed_log_excerpts": len(log_summaries),
            "sonar_issues": (context.get("sonar") or {}).get("total", 0),
        },
        "key_workflows": key_workflows,
        "workflows_needing_attention": workflows_needing_attention,
        "checks_needing_attention": checks_needing_attention,
        "failed_logs": log_summaries,
        "additional_ci_recommendations": recommendations,
        "sonar": context.get("sonar"),
    }


def _path_matches_prefixes(path: str, prefixes: list[str]) -> bool:
    normalized = normalize_path(path)
    for prefix in prefixes:
        token = normalize_path(str(prefix))
        if not token:
            continue
        if normalized == token.rstrip("/") or normalized.startswith(token):
            return True
    return False


def _rule_matches_changed_files(rule: dict[str, Any], changed_files: list[str]) -> bool:
    when = str(rule.get("when") or "")
    if when == "package_related":
        return any(is_package_related_path(path) for path in changed_files)
    prefixes = [str(item) for item in (rule.get("when_path_prefixes") or [])]
    paths = {normalize_path(str(item)) for item in (rule.get("when_paths") or [])}
    path_prefix_starts = [str(item) for item in (rule.get("when_path_prefixes_also") or [])]
    if prefixes or paths or path_prefix_starts:
        return any(
            path in paths
            or _path_matches_prefixes(path, prefixes)
            or _path_matches_prefixes(path, path_prefix_starts)
            for path in changed_files
        )
    return True


def recommend_additional_ci(
    context: dict[str, Any],
    key_workflow_runs: dict[str, dict[str, Any]],
    workflows_needing_attention: list[dict[str, Any]],
    ci_config: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    for name, run in key_workflow_runs.items():
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status == "not_run":
            recommendations.append(
                {
                    "action": "run_workflow",
                    "target": name,
                    "reason": f"{name} has no run for this PR head SHA.",
                }
            )
        elif conclusion in {"cancelled", "timed_out", "failure", "startup_failure"}:
            recommendations.append(
                {
                    "action": "rerun_workflow",
                    "target": name,
                    "reason": f"{name} concluded with {conclusion}.",
                }
            )

    changed_files = [str(item.get("filename") or "") for item in context.get("files") or []]
    workflow_names = {str(item.get("name") or "") for item in context.get("workflow_runs") or []}
    config = ci_config if ci_config is not None else load_ci_config()
    for rule in config.get("recommendations") or []:
        action = str(rule.get("action") or "")
        target = str(rule.get("target") or "")
        reason = str(rule.get("reason") or "")
        if not action or not target:
            continue
        if not _rule_matches_changed_files(rule, changed_files):
            continue
        missing_workflow = str(rule.get("missing_workflow") or "")
        if missing_workflow and missing_workflow in workflow_names:
            continue
        missing_any = {str(item) for item in (rule.get("missing_any_workflows") or [])}
        if missing_any and missing_any & workflow_names:
            continue
        recommendations.append(
            {
                "action": action,
                "target": target,
                "reason": reason or f"{action} {target}",
            }
        )

    for workflow in workflows_needing_attention:
        conclusion = str(workflow.get("conclusion") or "")
        if conclusion in {"cancelled", "timed_out"}:
            recommendations.append(
                {
                    "action": "rerun_workflow",
                    "target": str(workflow.get("name") or "unknown workflow"),
                    "reason": f"Workflow concluded with {conclusion}; rerun can distinguish transient cancellation from a real failure.",
                }
            )
    return dedupe_recommendations(recommendations)


def dedupe_recommendations(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for item in items:
        key = (item.get("action"), item.get("target"), item.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def matches(path: str, pattern: str) -> bool:
    path = normalize_path(path)
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3])
    if pattern.startswith("**/"):
        return path.endswith(pattern[3:]) or f"/{pattern[3:]}" in path
    if pattern.endswith("*"):
        return path.startswith(pattern[:-1])
    return path == pattern


def is_package_related_path(path: str) -> bool:
    path = normalize_path(path)
    return (
        path.startswith(("sdk/", "packages/"))
        or path
        in {
            "pyproject.toml",
            "uv.lock",
        }
        or path.endswith(
            (
                "/pyproject.toml",
                "/uv.lock",
                "/package.json",
                "/pnpm-lock.yaml",
                "/pnpm-workspace.yaml",
            )
        )
    )


def is_prompt_or_agent_path(path: str) -> bool:
    path = normalize_path(path)
    return (
        path == "AGENTS.md"
        or path.startswith(
            (
                ".agents/skills/",
                ".cursor/agents/",
                ".cursor/rules/",
                ".cursor/skills/",
                ".github/codex/schemas/",
                "codex/skills/",
                "backend/src/backend/evaluation/",
                "backend/tests/evaluators/",
            )
        )
        or "/prompts/" in path
        or "/assertions/" in path
    )


def is_infra_path(path: str) -> bool:
    path = normalize_path(path)
    return (
        path.startswith(
            (
                ".devcontainer/",
                "db/",
                "helm/",
                "keycloak/",
                "local-install/",
                "ssl/",
            )
        )
        or path.startswith("docker-compose")
        or "/docker-compose" in path
        or path == "Dockerfile"
        or path.endswith("/Dockerfile")
        or path == "Makefile"
        or path == "action.yml"
        or path == "renovate.json"
        or path.endswith(".sh")
        or path.endswith((".yaml", ".yml"))
        and path.startswith((".github/", "helm/"))
    )


def validate_reviewer_name(name: str) -> str:
    if not REVIEWER_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid reviewer name {name!r}; expected {REVIEWER_NAME_RE.pattern}")
    return name


DEFAULT_NORMAL_MODEL = "gpt-5.6-luna"
DEFAULT_NORMAL_EFFORT = "medium"
DEFAULT_CRITICAL_MODEL = "gpt-5.6-terra"
DEFAULT_CRITICAL_EFFORT = "high"
DEFAULT_AGGREGATOR_MODEL = "gpt-5.6-terra"
DEFAULT_AGGREGATOR_EFFORT = "high"


def _reviewer_entry(
    name: str,
    model_kind: str = "normal",
    role_references: list[str] | None = None,
    *,
    model: str | None = None,
    effort: str | None = None,
    normal_model: str = DEFAULT_NORMAL_MODEL,
    normal_effort: str = DEFAULT_NORMAL_EFFORT,
    critical_model: str = DEFAULT_CRITICAL_MODEL,
    critical_effort: str = DEFAULT_CRITICAL_EFFORT,
) -> dict[str, str]:
    safe_name = validate_reviewer_name(name)
    refs = parse_role_references(role_references or [])
    resolved_model = (
        model.strip()
        if model and model.strip()
        else (critical_model if model_kind == "critical" else normal_model)
    )
    resolved_effort = (
        effort.strip()
        if effort and effort.strip()
        else (critical_effort if model_kind == "critical" else normal_effort)
    )
    return {
        "name": safe_name,
        "prompt_file": f".cursor/agents/{safe_name}.md",
        "model_kind": model_kind,
        "model": resolved_model,
        "effort": resolved_effort,
        "role_references": ",".join(refs),
    }


def parse_role_references(values: list[Any]) -> list[str]:
    parsed: list[str] = []
    for raw in values:
        name = str(raw).strip()
        if not name:
            continue
        if "/" in name or "\\" in name or name in {".", ".."} or ".." in name:
            raise ValueError(f"invalid role reference {name!r}; expected a basename")
        if Path(name).name != name:
            raise ValueError(f"invalid role reference {name!r}; expected a basename")
        parsed.append(name)
    return parsed


def _rule_matches_path(path: str, rule: dict[str, Any]) -> bool:
    path = normalize_path(path)
    names = tuple(rule.get("path_names") or ())
    if path in names or Path(path).name in names:
        return True
    prefixes = tuple(normalize_path(str(prefix)) for prefix in (rule.get("path_prefixes") or ()))
    suffixes = tuple(str(suffix) for suffix in (rule.get("path_suffixes") or ()))
    prefix_hit = any(
        path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes if prefix
    )
    suffix_hit = any(path.endswith(suffix) for suffix in suffixes)
    if prefixes and suffixes:
        if prefix_hit and suffix_hit:
            return True
    elif prefixes and prefix_hit:
        return True
    elif suffixes and suffix_hit:
        return True
    lowered = path.lower()
    for fragment in rule.get("path_contains") or ():
        if str(fragment).lower() in lowered:
            return True
    return False


def load_reviewer_map_config(map_path: str | Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    payload = load_json(map_path)
    if not isinstance(payload, dict):
        raise ValueError("reviewer map must be a JSON object")
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("reviewer map must include a non-empty 'rules' array")
    parsed: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("name"):
            raise ValueError("each reviewer map rule needs a name")
        validate_reviewer_name(str(rule["name"]))
        parsed.append(rule)
    defaults_raw = payload.get("defaults") or payload.get("models") or {}
    defaults: dict[str, str] = {}
    if isinstance(defaults_raw, dict):
        for k, v in defaults_raw.items():
            if isinstance(v, str) and v.strip():
                defaults[k] = v.strip()
    for top_key in (
        "normal_model",
        "normal_effort",
        "critical_model",
        "critical_effort",
        "aggregator_model",
        "aggregator_effort",
    ):
        val = payload.get(top_key)
        if isinstance(val, str) and val.strip():
            defaults[top_key] = val.strip()
    return parsed, defaults


def load_reviewer_map(map_path: str | Path) -> list[dict[str, Any]]:
    rules, _ = load_reviewer_map_config(map_path)
    return rules


def select_reviewers_from_map(
    changed_files: list[str],
    rules: list[dict[str, Any]],
    *,
    normal_model: str = DEFAULT_NORMAL_MODEL,
    normal_effort: str = DEFAULT_NORMAL_EFFORT,
    critical_model: str = DEFAULT_CRITICAL_MODEL,
    critical_effort: str = DEFAULT_CRITICAL_EFFORT,
) -> list[dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    for file_path in changed_files:
        path = normalize_path(file_path)
        for rule in rules:
            if not _rule_matches_path(path, rule):
                continue
            name = str(rule["name"])
            model_kind = str(rule.get("model_kind") or "normal")
            rule_model = str(rule.get("model") or "").strip() or None
            rule_effort = str(rule.get("effort") or "").strip() or None
            refs = parse_role_references(list(rule.get("role_references") or []))
            selected[name] = _reviewer_entry(
                name,
                model_kind,
                refs,
                model=rule_model,
                effort=rule_effort,
                normal_model=normal_model,
                normal_effort=normal_effort,
                critical_model=critical_model,
                critical_effort=critical_effort,
            )
            if len(selected) >= MAX_REVIEWERS:
                break
        if len(selected) >= MAX_REVIEWERS:
            break
    return sorted(selected.values(), key=lambda item: item["name"])


def resolve_reviewer_map_path(map_path: str | Path | None = None) -> Path | None:
    candidate = map_path or os.environ.get("CODEX_REVIEWER_MAP", "").strip() or None
    if not candidate:
        return None
    resolved = Path(candidate)
    if resolved.is_file():
        return resolved
    return None


def _resolve_llm_defaults(map_defaults: dict[str, str] | None = None) -> dict[str, str]:
    d = map_defaults or {}
    return {
        "normal_model": os.environ.get("CODEX_NORMAL_MODEL", "").strip()
        or d.get("normal_model", "")
        or DEFAULT_NORMAL_MODEL,
        "normal_effort": os.environ.get("CODEX_NORMAL_EFFORT", "").strip()
        or d.get("normal_effort", "")
        or DEFAULT_NORMAL_EFFORT,
        "critical_model": os.environ.get("CODEX_CRITICAL_MODEL", "").strip()
        or d.get("critical_model", "")
        or DEFAULT_CRITICAL_MODEL,
        "critical_effort": os.environ.get("CODEX_CRITICAL_EFFORT", "").strip()
        or d.get("critical_effort", "")
        or DEFAULT_CRITICAL_EFFORT,
        "aggregator_model": os.environ.get("CODEX_AGGREGATOR_MODEL", "").strip()
        or d.get("aggregator_model", "")
        or DEFAULT_AGGREGATOR_MODEL,
        "aggregator_effort": os.environ.get("CODEX_AGGREGATOR_EFFORT", "").strip()
        or d.get("aggregator_effort", "")
        or DEFAULT_AGGREGATOR_EFFORT,
    }


def select_reviewers(
    changed_files: list[str], map_path: str | Path | None = None
) -> list[dict[str, str]]:
    resolved = resolve_reviewer_map_path(map_path)
    if resolved is not None:
        rules, map_defaults = load_reviewer_map_config(resolved)
        llm_cfg = _resolve_llm_defaults(map_defaults)
        return select_reviewers_from_map(
            changed_files,
            rules,
            normal_model=llm_cfg["normal_model"],
            normal_effort=llm_cfg["normal_effort"],
            critical_model=llm_cfg["critical_model"],
            critical_effort=llm_cfg["critical_effort"],
        )

    llm_cfg = _resolve_llm_defaults()
    selected: dict[str, dict[str, str]] = {}

    def add(name: str, model_kind: str = "normal") -> None:
        selected[name] = _reviewer_entry(
            name,
            model_kind,
            normal_model=llm_cfg["normal_model"],
            normal_effort=llm_cfg["normal_effort"],
            critical_model=llm_cfg["critical_model"],
            critical_effort=llm_cfg["critical_effort"],
        )

    for file_path in changed_files:
        path = normalize_path(file_path)
        if path.startswith("src/") and path.endswith(".py"):
            add("python-builder")
        if path.startswith("backend/") and path.endswith(".py"):
            add("clean-arch-guard")
        if path.startswith(("backend/migrations/", "backend/migrations_control/")):
            add("migration-reviewer", "critical")
        if path.startswith("frontend/"):
            add("frontend-reviewer")
            if re.search(r"/(app|components)/|/(page|layout)\.tsx$|\.tsx$", path):
                add("user-advocate")
        if path.startswith(".github/workflows/") or path == "action.yml":
            add("security-auditor", "critical")
            add("infra-reviewer")
        if path.startswith("scripts/ci/"):
            add("security-auditor", "critical")
            add("infra-reviewer")
        if is_package_related_path(path):
            add("security-auditor", "critical")
            add("infra-reviewer")
        if is_infra_path(path):
            add("infra-reviewer")
        if is_prompt_or_agent_path(path):
            add("prompt-critic")
        if is_security_sensitive_path(path):
            add("security-auditor", "critical")

    return sorted(selected.values(), key=lambda item: item["name"])[:MAX_REVIEWERS]


def is_security_sensitive_path(path: str) -> bool:
    lowered = path.lower()
    keywords = (
        "auth",
        "permission",
        "permissions",
        "secret",
        "session",
        "token",
        "security",
        "keycloak",
    )
    return any(keyword in lowered for keyword in keywords)


def ensure_responses_endpoint(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("AZURE_AI_API_BASE must be an absolute HTTPS URL")
    if not host_is_allowed_azure(parsed.netloc, azure_host_allowlist()):
        allowed = ", ".join(azure_host_allowlist())
        raise ValueError(
            f"Responses endpoint host {parsed.netloc!r} is not in the Azure allowlist ({allowed})"
        )
    if parsed.path.endswith("/responses"):
        return base_url
    if parsed.path.endswith("/openai/v1"):
        return f"{base_url}/responses"
    if re.fullmatch(r"/api/projects/[^/]+", parsed.path):
        return f"{base_url}/openai/v1/responses"
    raise ValueError(
        "AZURE_AI_API_BASE must be a Responses-compatible endpoint ending "
        "in /responses, an Azure OpenAI v1 base ending in /openai/v1, "
        "or an Azure AI Foundry project URL ending in /api/projects/<name>"
    )


def extract_json_object(payload: str) -> Any:
    start = payload.find("{")
    end = payload.rfind("}")
    if start < 0 or end < start:
        raise ValueError("expected a JSON object in Codex catalog output")
    return json.loads(payload[start : end + 1])


def patch_azure_model_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    models = catalog.get("models")
    if not isinstance(models, list):
        raise ValueError("Codex model catalog must contain a models array")
    patched_slugs: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            continue
        slug = model.get("slug")
        if slug not in AZURE_INCOMPATIBLE_MODEL_SLUGS:
            continue
        model["use_responses_lite"] = False
        # openai/codex-action v1.12+ forbids `--config features.multi_agent_v2=...`
        # under drop-sudo. Keep Azure models on the catalog flag instead.
        model["multi_agent_version"] = None
        model["tool_mode"] = None
        patched_slugs.add(slug)
    missing = [slug for slug in REQUIRED_AZURE_CATALOG_SLUGS if slug not in patched_slugs]
    if missing:
        raise ValueError(
            "Codex catalog is missing required Azure-incompatible model(s): " + ", ".join(missing)
        )
    return catalog


def load_bundled_codex_catalog() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = os.environ.copy()
        env["CODEX_HOME"] = tmpdir
        completed = subprocess.run(
            ["codex", "debug", "models", "--bundled"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or "no output"
        raise RuntimeError(f"codex debug models --bundled failed: {detail}")
    catalog = extract_json_object(completed.stdout)
    if not isinstance(catalog, dict):
        raise ValueError("codex debug models did not return a JSON object")
    return catalog


def sanitize_review_body(body: str, title: str) -> str:
    body = "".join(char for char in body if char == "\n" or char == "\t" or ord(char) >= 32)
    body = redact_secret_references(body)
    body = strip_untrusted_links(body)
    body = defang_mentions(body)
    body = body.strip()
    if len(body) > MAX_REVIEW_BODY_CHARS:
        body = body[:MAX_REVIEW_BODY_CHARS] + "\n\n...[truncated]"
    trigger_note = review_trigger_note(title)
    header = (
        f"## {title}\n\n"
        "Automated advisory output from Codex. Validate findings before acting on them.\n"
        f"{trigger_note}"
    )
    return f"{header}\n{body}\n"


def review_trigger_note(title: str) -> str:
    if title == "Codex advisory code review":
        return (
            "To opt in or rerun this code review, add the `codex-review` label "
            "or comment `/review` as a trusted collaborator.\n"
        )
    if title == "Codex advisory CI analysis":
        return "To rerun CI analysis, comment `/codex-ci-assess` as a trusted collaborator.\n"
    return ""


def redact_secret_references(text: str) -> str:
    patterns = (
        r"(?i)([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY_ID|SECRET_ACCESS_KEY|CLIENT_SECRET)[A-Z0-9_]*\s*[:=]\s*)\S+",
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
        r"sk-[A-Za-z0-9_-]{12,}",
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
        r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    )
    result = text
    for pattern in patterns:
        result = re.sub(
            pattern,
            lambda match: match.group(1) + "[redacted]" if match.lastindex else "[redacted]",
            result,
        )
    return result


def strip_untrusted_links(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            host = urlparse(url).netloc.lower()
        except ValueError:
            return "[external-link-removed]"
        if host in ALLOWED_LINK_HOSTS:
            return url
        return "[external-link-removed]"

    return re.sub(r"https?://[^\s)>\]]+", replace_url, text)


def defang_mentions(text: str) -> str:
    return re.sub(
        r"(?<![\w.])@([A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[A-Za-z0-9_.-]+)?)",
        r"(at)\1",
        text,
    )


def post_review(repo: GitHubRepo, pr_number: int, body_path: Path) -> None:
    body = body_path.read_text(encoding="utf-8")
    payload = json.dumps({"event": "COMMENT", "body": body})
    run_text(
        [
            "gh",
            "api",
            f"repos/{repo.full_name}/pulls/{pr_number}/reviews",
            "-X",
            "POST",
            "--input",
            "-",
        ],
        check=True,
        stdin=payload,
    )


def main_resolve(args: argparse.Namespace) -> int:
    event_path = args.event_path or os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise SystemExit("--event-path is required when GITHUB_EVENT_PATH is unset")
    event = load_json(event_path)
    repo = repo_from_env()
    pr_number = resolve_pr_number(event, repo)
    outputs = {"should_run": "false"}
    if pr_number is not None:
        pr = get_pr(repo, pr_number)
        should_run = pr.get("state") == "open"
        if should_run and os.environ.get("CODEX_ASSESS_REQUIRE_SAME_REPO", "true") == "true":
            should_run = is_same_repo_pr(pr)
        if should_run and os.environ.get("GITHUB_EVENT_NAME") == "workflow_run":
            workflow_run = event.get("workflow_run") or {}
            should_run = workflow_run.get("head_sha") == pr["head"]["sha"]
        outputs = {
            "should_run": "true" if should_run else "false",
            "pr_number": str(pr_number),
            "head_sha": pr["head"]["sha"],
            "base_sha": pr["base"]["sha"],
        }
    if args.output:
        append_github_output(args.output, outputs)
    print(json.dumps(outputs, sort_keys=True))
    return 0


def add_resolve_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("resolve")
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--output")
    parser.set_defaults(func=main_resolve)


def main_collect_review(args: argparse.Namespace) -> int:
    write_json(args.output, collect_review_context(args.pr_number))
    return 0


def add_collect_review_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("collect-review")
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.set_defaults(func=main_collect_review)


def main_collect_ci(args: argparse.Namespace) -> int:
    write_json(args.output, collect_ci_context(args.pr_number))
    return 0


def add_collect_ci_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("collect-ci")
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.set_defaults(func=main_collect_ci)


def main_triage_ci(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    triage = triage_ci(context)
    write_json(args.output, triage)
    if args.github_output:
        append_github_output(
            args.github_output,
            {
                "ci_ready": "true" if triage["ready"] else "false",
                "ci_needs_ai": "true" if triage["needs_ai_diagnosis"] else "false",
            },
        )
    print(json.dumps(triage, sort_keys=True))
    return 0


def add_triage_ci_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("triage-ci")
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    parser.set_defaults(func=main_triage_ci)


def main_select(args: argparse.Namespace) -> int:
    context = load_json(args.context)
    changed_files = [item["filename"] for item in context.get("files", []) if item.get("filename")]
    resolved_map = resolve_reviewer_map_path(args.reviewer_map)
    map_defaults: dict[str, str] = {}
    if resolved_map is not None:
        _, map_defaults = load_reviewer_map_config(resolved_map)
    llm_cfg = _resolve_llm_defaults(map_defaults)
    reviewers = select_reviewers(changed_files, map_path=args.reviewer_map)
    payload = {
        "reviewers": reviewers,
        "aggregator_model": llm_cfg["aggregator_model"],
        "aggregator_effort": llm_cfg["aggregator_effort"],
    }
    write_json(args.output, payload)
    if args.github_output:
        values = {
            "has_reviewers": "true" if reviewers else "false",
            "reviewer_count": str(len(reviewers)),
            "aggregator_model": llm_cfg["aggregator_model"],
            "aggregator_effort": llm_cfg["aggregator_effort"],
            "matrix": json.dumps({"include": reviewers}, separators=(",", ":")),
        }
        for index in range(MAX_REVIEWERS):
            if index < len(reviewers):
                values[f"reviewer_{index}_name"] = reviewers[index]["name"]
                values[f"reviewer_{index}_model_kind"] = reviewers[index]["model_kind"]
                values[f"reviewer_{index}_model"] = reviewers[index]["model"]
                values[f"reviewer_{index}_effort"] = reviewers[index]["effort"]
                values[f"reviewer_{index}_refs"] = reviewers[index].get("role_references", "")
            else:
                values[f"reviewer_{index}_name"] = ""
                values[f"reviewer_{index}_model_kind"] = ""
                values[f"reviewer_{index}_model"] = ""
                values[f"reviewer_{index}_effort"] = ""
                values[f"reviewer_{index}_refs"] = ""
        append_github_output(args.github_output, values)
    print(json.dumps(payload, sort_keys=True))
    return 0


def add_select_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("select")
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    parser.add_argument("--reviewer-map")
    parser.set_defaults(func=main_select)


def main_endpoint(args: argparse.Namespace) -> int:
    try:
        endpoint = ensure_responses_endpoint(args.base_url)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if args.github_output:
        append_github_output(args.github_output, {"responses_endpoint": endpoint})
    print("Responses endpoint validated")
    return 0


def add_endpoint_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("endpoint")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--github-output")
    parser.set_defaults(func=main_endpoint)


def main_prepare_azure_catalog(args: argparse.Namespace) -> int:
    try:
        if args.catalog:
            catalog = load_json(args.catalog)
            if not isinstance(catalog, dict):
                raise ValueError("Codex model catalog must be a JSON object")
        else:
            catalog = load_bundled_codex_catalog()
        patched = patch_azure_model_catalog(catalog)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    write_json(args.output, patched)
    print(f"Wrote Azure-compatible Codex catalog to {args.output}")
    return 0


def add_prepare_azure_catalog_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("prepare-azure-catalog")
    parser.add_argument(
        "--catalog",
        help="Optional Codex catalog JSON. Defaults to `codex debug models --bundled`.",
    )
    parser.add_argument("--output", required=True)
    parser.set_defaults(func=main_prepare_azure_catalog)


def main_sanitize_review(args: argparse.Namespace) -> int:
    body = Path(args.input).read_text(encoding="utf-8")
    Path(args.output).write_text(
        sanitize_review_body(body, args.title),
        encoding="utf-8",
    )
    return 0


def add_sanitize_review_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("sanitize-review")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", required=True)
    parser.set_defaults(func=main_sanitize_review)


def main_post_review(args: argparse.Namespace) -> int:
    post_review(repo_from_env(), args.pr_number, Path(args.body_file))
    return 0


def add_post_review_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("post-review")
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--body-file", required=True)
    parser.set_defaults(func=main_post_review)


def main_stage_overlay(args: argparse.Namespace) -> int:
    name = validate_reviewer_name(args.name)
    trusted = Path(args.trusted_config)
    overlay = trusted / ".cursor" / "agents" / f"{name}.md"
    if not overlay.is_file():
        print(f"missing reviewer overlay: {overlay}", file=sys.stderr)
        return 1
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(overlay.read_text(encoding="utf-8"), encoding="utf-8")
    refs_dir = Path(args.references_output)
    if refs_dir.exists():
        for child in refs_dir.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                raise ValueError(f"unexpected directory in role-references: {child}")
    refs_dir.mkdir(parents=True, exist_ok=True)
    source_dir = trusted / ".cursor" / "agents" / "references"
    for basename in parse_role_references(
        [part for part in args.role_references.split(",") if part]
    ):
        source = source_dir / basename
        if not source.is_file():
            print(f"missing role reference: {source}", file=sys.stderr)
            return 1
        (refs_dir / basename).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


def add_stage_overlay_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("stage-overlay")
    parser.add_argument("--name", required=True)
    parser.add_argument("--trusted-config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--references-output", required=True)
    parser.add_argument("--role-references", default="")
    parser.set_defaults(func=main_stage_overlay)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex PR assessment CI helper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_resolve_parser(subparsers)
    add_collect_review_parser(subparsers)
    add_collect_ci_parser(subparsers)
    add_triage_ci_parser(subparsers)
    add_select_parser(subparsers)
    add_endpoint_parser(subparsers)
    add_prepare_azure_catalog_parser(subparsers)
    add_sanitize_review_parser(subparsers)
    add_post_review_parser(subparsers)
    add_stage_overlay_parser(subparsers)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
