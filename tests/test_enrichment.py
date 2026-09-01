"""Tests for enrichment.py — mocked urlopen."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from release_notes.enrichment import fetch_github_issue


def _make_response(data: dict, status: int = 200):
    """Return a context-manager mock that yields a response-like object."""
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestFetchGithubIssue:
    def test_success_issue(self):
        data = {
            "number": 42,
            "title": "Fix the thing",
            "html_url": "https://github.com/org/repo/issues/42",
            "body": "Some description",
            "state": "closed",
            "labels": [{"name": "bug"}],
        }
        with patch("release_notes.enrichment.urlopen", return_value=_make_response(data)):
            issue = fetch_github_issue(42, owner_repo="org/repo", token="tok")

        assert issue is not None
        assert issue.number == 42
        assert issue.title == "Fix the thing"
        assert issue.is_pull_request is False
        assert issue.labels == ["bug"]

    def test_success_pull_request(self):
        data = {
            "number": 7,
            "title": "Add feature",
            "html_url": "https://github.com/org/repo/pull/7",
            "body": "",
            "state": "closed",
            "labels": [],
            "pull_request": {"merged_at": "2024-01-01"},
        }
        with patch("release_notes.enrichment.urlopen", return_value=_make_response(data)):
            issue = fetch_github_issue(7, owner_repo="org/repo", token="tok")

        assert issue is not None
        assert issue.is_pull_request is True

    def test_403_returns_none(self):
        err = HTTPError(url="", code=403, msg="Forbidden", hdrs=None, fp=None)
        with patch("release_notes.enrichment.urlopen", side_effect=err):
            result = fetch_github_issue(1, owner_repo="org/repo", token="tok")
        assert result is None

    def test_404_returns_none(self):
        err = HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)
        with patch("release_notes.enrichment.urlopen", side_effect=err):
            result = fetch_github_issue(99, owner_repo="org/repo", token="tok")
        assert result is None

    def test_500_raises(self):
        err = HTTPError(url="", code=500, msg="Server Error", hdrs=None, fp=None)
        with patch("release_notes.enrichment.urlopen", side_effect=err):
            with pytest.raises(SystemExit):
                fetch_github_issue(1, owner_repo="org/repo", token="tok")
