"""Issue tracker enrichment (Linear, GitHub)."""

import json
import logging
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import GitHubIssue, LinearIssue

LOG = logging.getLogger("release_notes.enrichment")


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8", errors="replace"))


def _truncate(s: str, max_bytes: int) -> tuple[str, bool]:
    b = s.encode("utf-8", errors="replace")
    if len(b) <= max_bytes:
        return s, False
    cut = b[: max_bytes - 50].decode("utf-8", errors="replace")
    return f"{cut}\n\n...[TRUNCATED]...\n", True


def fetch_linear_issue(identifier: str, *, api_key: str) -> LinearIssue:
    query = """
    query Issue($id: String!) {
      issue(id: $id) {
        identifier
        title
        url
        description
      }
    }
    """.strip()
    payload = {"query": query, "variables": {"id": identifier}}
    req = Request(
        "https://api.linear.app/graphql",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise SystemExit(f"Linear API HTTP error for {identifier}: {e.code} {e.reason}") from e
    except URLError as e:
        raise SystemExit(f"Linear API network error for {identifier}: {e}") from e

    try:
        data = json.loads(body)
    except Exception as e:
        raise SystemExit(f"Linear API returned invalid JSON for {identifier}") from e

    if isinstance(data, dict) and data.get("errors"):
        raise SystemExit(f"Linear API GraphQL errors for {identifier}: {data['errors']}")

    issue = (data.get("data") or {}).get("issue") if isinstance(data, dict) else None
    if not issue:
        raise SystemExit(f"Linear issue not found: {identifier}")

    return LinearIssue(
        identifier=str(issue.get("identifier") or identifier),
        title=str(issue.get("title") or ""),
        url=str(issue.get("url") or ""),
        description=str(issue.get("description") or ""),
    )


def fetch_linear_issues(
    identifiers: Iterable[str], *, api_key: str, max_issue_bytes: int
) -> dict[str, LinearIssue]:
    out: dict[str, LinearIssue] = {}
    ids = sorted({i.strip() for i in identifiers if i and i.strip()})
    if not ids:
        return out

    LOG.info("Fetching Linear issues: %d", len(ids))
    for ident in ids:
        issue = fetch_linear_issue(ident, api_key=api_key)
        desc_t, was_truncated = _truncate(issue.description or "", max_issue_bytes)
        if was_truncated:
            LOG.info("Truncated Linear description: %s cap=%dB", ident, max_issue_bytes)
        out[ident] = LinearIssue(
            identifier=issue.identifier,
            title=issue.title,
            url=issue.url,
            description=desc_t,
        )
    return out


# --- GitHub issue/PR enrichment ---


def fetch_github_issue(
    number: int, *, owner_repo: str, token: str
) -> GitHubIssue | None:
    """Fetch a single GitHub issue or PR via the REST API."""
    url = f"https://api.github.com/repos/{owner_repo}/issues/{number}"
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        if e.code in (403, 404):
            LOG.warning("GitHub issue #%d not accessible in %s (%d %s), skipping.", number, owner_repo, e.code, e.reason)
            return None
        raise SystemExit(
            f"GitHub API HTTP error for #{number}: {e.code} {e.reason}"
        ) from e
    except URLError as e:
        raise SystemExit(f"GitHub API network error for #{number}: {e}") from e

    data = json.loads(body)
    labels = [lbl["name"] for lbl in data.get("labels", []) if isinstance(lbl, dict)]
    is_pr = "pull_request" in data

    return GitHubIssue(
        number=int(data["number"]),
        title=str(data.get("title") or ""),
        url=str(data.get("html_url") or ""),
        body=str(data.get("body") or ""),
        is_pull_request=is_pr,
        state=str(data.get("state") or ""),
        labels=labels,
    )


def fetch_github_issues(
    numbers: Iterable[int],
    *,
    owner_repo: str,
    token: str,
    max_issue_bytes: int,
) -> dict[int, GitHubIssue]:
    """Fetch multiple GitHub issues/PRs, keyed by number."""
    out: dict[int, GitHubIssue] = {}
    nums = sorted({n for n in numbers if n})
    if not nums:
        return out

    LOG.info("Fetching GitHub issues: %d from %s", len(nums), owner_repo)
    for n in nums:
        issue = fetch_github_issue(n, owner_repo=owner_repo, token=token)
        if issue is None:
            continue
        body_t, was_truncated = _truncate(issue.body or "", max_issue_bytes)
        if was_truncated:
            LOG.info("Truncated GitHub body: #%d cap=%dB", n, max_issue_bytes)
        out[n] = GitHubIssue(
            number=issue.number,
            title=issue.title,
            url=issue.url,
            body=body_t,
            is_pull_request=issue.is_pull_request,
            state=issue.state,
            labels=issue.labels,
        )
    return out
