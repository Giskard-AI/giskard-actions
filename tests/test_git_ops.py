"""Tests for pure functions in git_ops.py (no subprocess calls)."""

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


class TestGitShow:
    def _git(self, repo, *args):
        import subprocess

        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    def test_regular_file_at_ref(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "t@example.com")
        self._git(repo, "config", "user.name", "t")
        (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
        self._git(repo, "add", "pyproject.toml")
        self._git(repo, "commit", "-m", "init")
        from release_notes.git_ops import git_show

        content = git_show("HEAD", "pyproject.toml", repo_root=repo)
        assert content is not None
        assert "name='x'" in content

    def test_missing_file_returns_none(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "t@example.com")
        self._git(repo, "config", "user.name", "t")
        (repo / "README.md").write_text("hi\n")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "init")
        from release_notes.git_ops import git_show

        assert git_show("HEAD", "nope.toml", repo_root=repo) is None

    def test_gitlink_nested_file(self, tmp_path):
        import subprocess

        from release_notes.git_ops import git_show, gitlink_sha

        child = tmp_path / "child"
        parent = tmp_path / "parent"
        child.mkdir()
        parent.mkdir()
        self._git(child, "init")
        self._git(child, "config", "user.email", "t@example.com")
        self._git(child, "config", "user.name", "t")
        (child / "pyproject.toml").write_text("[project]\nname='lidar'\n")
        self._git(child, "add", "pyproject.toml")
        self._git(child, "commit", "-m", "child")
        self._git(parent, "init")
        self._git(parent, "config", "user.email", "t@example.com")
        self._git(parent, "config", "user.name", "t")
        (parent / "README.md").write_text("root\n")
        self._git(parent, "add", "README.md")
        self._git(parent, "commit", "-m", "root")
        subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(child),
                "vendor/mod",
            ],
            cwd=parent,
            check=True,
            capture_output=True,
        )
        self._git(parent, "commit", "-m", "add submodule")
        sha = gitlink_sha("HEAD", "vendor/mod", repo_root=parent)
        assert sha
        content = git_show("HEAD", "vendor/mod/pyproject.toml", repo_root=parent)
        assert content is not None
        assert "lidar" in content


class TestDetectTagRange:
    def _git(self, repo, *args):
        import subprocess

        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    def _init_repo_with_tags(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "t@example.com")
        self._git(repo, "config", "user.name", "t")
        (repo / "README.md").write_text("v1\n")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "c1")
        self._git(repo, "tag", "v0.1.0")
        (repo / "README.md").write_text("v2\n")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "c2")
        self._git(repo, "tag", "v0.2.0")
        (repo / "README.md").write_text("unreleased\n")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-m", "c3")
        return repo

    def test_existing_tag_resolves_previous(self, tmp_path):
        from release_notes.git_ops import detect_tag_range

        repo = self._init_repo_with_tags(tmp_path)
        tr = detect_tag_range("0.2.0", None, repo, dry_run=False)
        assert tr.previous_tag == "v0.1.0"
        assert tr.current_tag == "v0.2.0"

    def test_dry_run_missing_tag_falls_back_to_head_and_latest_tag(self, tmp_path):
        from release_notes.git_ops import detect_tag_range

        repo = self._init_repo_with_tags(tmp_path)
        self._git(repo, "tag", "vv.0.0.1-custom-ds")
        tr = detect_tag_range("0.0.0-pr", None, repo, dry_run=True)
        assert tr.previous_tag == "v0.2.0"
        assert tr.current_tag == "HEAD"

    def test_non_dry_run_missing_tag_raises(self, tmp_path):
        import pytest

        from release_notes.git_ops import detect_tag_range

        repo = self._init_repo_with_tags(tmp_path)
        with pytest.raises(SystemExit):
            detect_tag_range("0.0.0-pr", None, repo, dry_run=False)
