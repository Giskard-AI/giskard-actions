"""Tests for GitHub Actions output sanitization."""

import json
import subprocess
from pathlib import Path

import pytest
from write_github_output import (
    main,
    resolve_notes_output_path,
    sanitize_scalar,
    write_github_outputs,
    write_metadata_outputs,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("release_notes/v1.0.0.md", "release_notes/v1.0.0.md"),
        ("evil\nextra=1", "evilextra=1"),
        ("evil\r\nextra=1", "evilextra=1"),
    ],
    ids=["plain-path", "strips-lf", "strips-crlf"],
)
def test_sanitize_scalar_strips_newlines(raw: str, expected: str) -> None:
    assert sanitize_scalar(raw) == expected


@pytest.mark.parametrize(
    ("output_path", "version", "expected"),
    [
        ("custom/out.md", "1.0.0", "custom/out.md"),
        ("", "2.3.0", "release_notes/v2.3.0.md"),
        ("custom\n/out.md", "1.0.0", "custom/out.md"),
        ("", "2.3.0\ninjected=1", "release_notes/v2.3.0injected=1.md"),
    ],
    ids=["explicit-path", "default-from-version", "path-strips-newline", "version-strips-newline"],
)
def test_resolve_notes_output_path(output_path: str, version: str, expected: str) -> None:
    assert resolve_notes_output_path(output_path, version) == expected


def test_write_github_outputs_scalar_cannot_inject_keys(tmp_path: Path) -> None:
    target = tmp_path / "github-output"
    write_github_outputs(
        target,
        scalars={"output": "notes.md\ncompare_url=https://evil.example"},
    )
    text = target.read_text(encoding="utf-8")
    assert text == "output=notes.mdcompare_url=https://evil.example\n"
    assert text.count("\n") == 1


def test_write_metadata_outputs_uses_unique_delimiter_when_summary_contains_eofmeta(
    tmp_path: Path,
) -> None:
    target = tmp_path / "github-output"
    write_metadata_outputs(
        target,
        {
            "output_path": "notes.md",
            "compare_url": "https://github.com/example/compare",
            "summary": "hello\nEOFMETA\nworld",
        },
    )
    text = target.read_text(encoding="utf-8")
    assert "notes_path=notes.md\n" in text
    assert "summary<<EOFMETA\n" not in text
    assert "hello" in text
    assert "world" in text


def test_main_resolve_output_path_reads_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_OUTPUT_PATH", "out\n/x.md")
    monkeypatch.setenv("INPUT_VERSION", "1.0.0")
    assert main(["--github-output", str(target), "--resolve-output-path"]) == 0
    assert target.read_text(encoding="utf-8") == "output=out/x.md\n"


def test_main_metadata_file(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "output_path": "notes.md",
                "compare_url": "https://example.test",
                "summary": "line1\nline2",
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "github-output"
    assert main(["--github-output", str(target), "--metadata-file", str(metadata)]) == 0
    text = target.read_text(encoding="utf-8")
    assert "notes_path=notes.md\n" in text
    assert "compare_url=https://example.test\n" in text
    assert "line1" in text
    assert "line2" in text


def _init_git_repo(repo_dir: Path, pyproject_version: str = "0.3.0") -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    (repo_dir / "pyproject.toml").write_text(
        f'[project]\nname = "giskard-actions"\nversion = "{pyproject_version}"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "pyproject.toml"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "chore: initial commit"],
        cwd=repo_dir,
        check=True,
    )


def _commit_empty(repo_dir: Path, message: str) -> None:
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message],
        cwd=repo_dir,
        check=True,
    )


@pytest.mark.parametrize(
    ("initial_tags", "commits_after_tag", "expected_outputs"),
    [
        (
            [],
            [],
            {
                "should_release": "true",
                "tag": "v0.3.0",
                "previous_tag": "",
            },
        ),
        (
            ["v0.3.0"],
            [],
            {
                "should_release": "false",
                "tag": "v0.3.0",
                "previous_tag": "v0.3.0",
            },
        ),
        (
            ["v0.3.0"],
            ["fix: handle empty diff"],
            {
                "should_release": "true",
                "tag": "v0.3.1",
                "previous_tag": "v0.3.0",
            },
        ),
        (
            ["v0.3.0"],
            [
                "fix: small tweak",
                "feat(codex): add effort input",
                "Merge pull request #25 from Giskard-AI/feat/effort",
            ],
            {
                "should_release": "true",
                "tag": "v0.4.0",
                "previous_tag": "v0.3.0",
            },
        ),
        (
            ["v0.3.0"],
            ["feat!: drop legacy input"],
            {
                "should_release": "true",
                "tag": "v1.0.0",
                "previous_tag": "v0.3.0",
            },
        ),
        (
            ["v0.3.0"],
            ["refactor: restructure inputs\n\nBREAKING CHANGE: input x renamed to y"],
            {
                "should_release": "true",
                "tag": "v1.0.0",
                "previous_tag": "v0.3.0",
            },
        ),
        (
            ["v0.2.9", "v0.3.0", "test-tag", "v0.0.0-pr"],
            ["chore(deps): update ruff\n\nNon-breaking dependency bump"],
            {
                "should_release": "true",
                "tag": "v0.3.1",
                "previous_tag": "v0.3.0",
            },
        ),
    ],
    ids=[
        "bootstrap_no_tags_uses_pyproject_version",
        "head_already_tagged_is_noop",
        "single_fix_commit_bumps_patch",
        "multi_commit_merge_range_with_feat_bumps_minor",
        "bang_header_bumps_major",
        "breaking_change_footer_bumps_major",
        "ignores_non_semver_tags_and_sorts_semver_properly",
    ],
)
def test_compute_next_release_tag_cli_seam(
    tmp_path: Path,
    initial_tags: list[str],
    commits_after_tag: list[str],
    expected_outputs: dict[str, str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir, pyproject_version="0.3.0")
    for tag in initial_tags:
        subprocess.run(["git", "tag", tag], cwd=repo_dir, check=True)
    for msg in commits_after_tag:
        _commit_empty(repo_dir, msg)

    output_file = tmp_path / "github-output"
    rc = main(
        [
            "--github-output",
            str(output_file),
            "--compute-next-release-tag",
            "--repo-root",
            str(repo_dir),
        ]
    )
    assert rc == 0
    lines = dict(
        line.split("=", 1) for line in output_file.read_text(encoding="utf-8").strip().splitlines()
    )
    assert lines == expected_outputs
