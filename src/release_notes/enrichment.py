"""Issue tracker enrichment (Linear, GitHub)."""

import json
import logging
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import LinearIssue

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
