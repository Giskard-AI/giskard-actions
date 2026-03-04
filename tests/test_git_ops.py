"""Tests for pure functions in git_ops.py (no subprocess calls)."""

import pytest

from release_notes.git_ops import (
    _filter_diff_stat,
    _filter_name_status,
    _score_path,
    extract_github_issue_numbers,
    extract_linear_keys,
    parse_pr_number,
)


class TestExtractLinearKeys:
    def test_single_key(self):
        assert extract_linear_keys("Fix [ENG-123] crash") == ["ENG-123"]

    def test_multiple_keys(self):
        assert extract_linear_keys("[ENG-1] [FRONT-42] something") == ["ENG-1", "FRONT-42"]

    def test_deduplicates(self):
        assert extract_linear_keys("[ENG-1] and also [ENG-1]") == ["ENG-1"]

    def test_no_keys(self):
        assert extract_linear_keys("no keys here") == []

    def test_empty_string(self):
        assert extract_linear_keys("") == []

    def test_none_safe(self):
        assert extract_linear_keys(None) == []  # type: ignore[arg-type]

    def test_preserves_order(self):
        assert extract_linear_keys("[ZZZ-9] then [AAA-1]") == ["ZZZ-9", "AAA-1"]


class TestExtractGithubIssueNumbers:
    def test_body_fix_reference(self):
        body = "Fixes #42\nSome text"
        assert 42 in extract_github_issue_numbers(body)

    def test_close_reference(self):
        assert 7 in extract_github_issue_numbers("Closes #7")

    def test_resolve_reference(self):
        assert 99 in extract_github_issue_numbers("Resolves #99")

    def test_plain_hash_reference(self):
        assert 10 in extract_github_issue_numbers("see #10 for details")

    def test_excludes_pr_trailing_paren(self):
        # "(#456)" at end of subject is handled by parse_pr_number, not here
        result = extract_github_issue_numbers("(#456)")
        assert 456 not in result

    def test_deduplicates(self):
        result = extract_github_issue_numbers("Fixes #5 and also #5")
        assert result.count(5) == 1

    def test_empty(self):
        assert extract_github_issue_numbers("") == []


class TestParsePrNumber:
    def test_standard_format(self):
        assert parse_pr_number("Some feature (#123)") == 123

    def test_no_pr(self):
        assert parse_pr_number("Just a commit") is None

    def test_not_at_end(self):
        assert parse_pr_number("(#123) at the start") is None

    def test_empty(self):
        assert parse_pr_number("") is None

    def test_none_safe(self):
        assert parse_pr_number(None) is None  # type: ignore[arg-type]


class TestFilterDiffStat:
    def test_removes_ignored_basename(self):
        raw = "uv.lock                | 10 ++++\nfoo/bar.py             | 5 ++\n 2 files changed"
        result = _filter_diff_stat(raw, ignored_basenames={"uv.lock"})
        assert "uv.lock" not in result
        assert "bar.py" in result

    def test_keeps_summary_line(self):
        raw = "foo.py | 3 +++\n 1 file changed, 3 insertions(+)"
        result = _filter_diff_stat(raw, ignored_basenames=set())
        assert "1 file changed" in result

    def test_empty_input(self):
        assert _filter_diff_stat("", ignored_basenames={"uv.lock"}) == ""

    def test_multiple_ignored(self):
        raw = "pnpm-lock.yaml | 20 ++++\nuv.lock | 5 +\nREADME.md | 1 +\n"
        result = _filter_diff_stat(raw, ignored_basenames={"pnpm-lock.yaml", "uv.lock"})
        assert "pnpm-lock.yaml" not in result
        assert "uv.lock" not in result
        assert "README.md" in result


class TestFilterNameStatus:
    def test_removes_ignored(self):
        raw = "M\tuv.lock\nA\tsrc/foo.py\n"
        result = _filter_name_status(raw, ignored_basenames={"uv.lock"})
        assert "uv.lock" not in result
        assert "src/foo.py" in result

    def test_keeps_all_when_no_match(self):
        raw = "M\tsrc/foo.py\nA\tsrc/bar.py\n"
        result = _filter_name_status(raw, ignored_basenames=set())
        assert "src/foo.py" in result
        assert "src/bar.py" in result

    def test_empty_input(self):
        assert _filter_name_status("", ignored_basenames={"uv.lock"}) == ""


class TestScorePath:
    def test_first_prefix_returns_zero(self):
        assert _score_path("src/foo.py", ("src/", "tests/")) == 0

    def test_second_prefix_returns_one(self):
        assert _score_path("tests/bar.py", ("src/", "tests/")) == 1

    def test_no_match_returns_fifty(self):
        assert _score_path("docs/readme.md", ("src/", "tests/")) == 50

    def test_empty_prefixes(self):
        assert _score_path("anything.py", ()) == 50
