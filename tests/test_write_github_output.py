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


def _release(tag: str, previous: str) -> dict[str, str]:
    return {"should_release": "true", "tag": tag, "previous_tag": previous}


def _no_release(tag: str) -> dict[str, str]:
    return {"should_release": "false", "tag": tag, "previous_tag": tag}


@pytest.mark.parametrize(
    ("initial_tags", "commits_after_tag", "expected_outputs"),
    [
        pytest.param([], [], _release("v0.3.0", ""), id="bootstrap_no_tags_uses_pyproject_version"),
        pytest.param(["v0.3.0"], [], _no_release("v0.3.0"), id="head_already_tagged_is_noop"),
        pytest.param(
            ["v0.3.0"],
            ["fix: handle empty diff"],
            _release("v0.3.1", "v0.3.0"),
            id="single_fix_commit_bumps_patch",
        ),
        pytest.param(
            ["v0.3.0"],
            ["perf(git): batch log calls"],
            _release("v0.3.1", "v0.3.0"),
            id="perf_commit_bumps_patch",
        ),
        pytest.param(
            ["v0.3.0"],
            [
                "fix: small tweak",
                "feat(codex): add effort input",
                "Merge pull request #25 from octo-org/feat/effort",
            ],
            _release("v0.4.0", "v0.3.0"),
            id="multi_commit_merge_range_with_feat_bumps_minor",
        ),
        pytest.param(
            ["v0.3.0"],
            ["feat!: drop legacy input"],
            _release("v0.4.0", "v0.3.0"),
            id="bang_header_on_0x_bumps_minor",
        ),
        pytest.param(
            ["v1.2.3"],
            ["feat!: drop legacy input"],
            _release("v2.0.0", "v1.2.3"),
            id="bang_header_on_1x_bumps_major",
        ),
        pytest.param(
            ["v0.3.0"],
            ["refactor: restructure inputs\n\nBREAKING CHANGE: input x renamed to y"],
            _release("v0.4.0", "v0.3.0"),
            id="breaking_change_footer_on_0x_bumps_minor",
        ),
        pytest.param(
            ["v1.2.3"],
            ["refactor: restructure inputs\n\nBREAKING-CHANGE: input x renamed to y"],
            _release("v2.0.0", "v1.2.3"),
            id="hyphenated_breaking_change_footer_on_1x_bumps_major",
        ),
        pytest.param(
            ["v1.2.3"],
            ["fix: clarify docs\n\nThis is not a BREAKING CHANGE: callers are unaffected."],
            _release("v1.2.4", "v1.2.3"),
            id="breaking_change_mid_line_is_not_a_footer",
        ),
        pytest.param(
            ["v0.2.9", "v0.3.0", "test-tag", "v0.0.0-pr"],
            ["fix(deps): update ruff\n\nNon-breaking dependency bump"],
            _release("v0.3.1", "v0.3.0"),
            id="ignores_non_semver_tags_and_sorts_semver_properly",
        ),
        pytest.param(
            ["v0.3.0"],
            ["chore: tidy scripts", "docs: fix typo", "ci: cache uv", "Merge branch 'main'"],
            _no_release("v0.3.0"),
            id="no_feat_fix_perf_deps_or_breaking_commit_skips_release",
        ),
        pytest.param(
            ["v0.3.0"],
            ["chore: update lockfiles"],
            _no_release("v0.3.0"),
            id="chore_without_scope_does_not_release",
        ),
        pytest.param(
            ["v0.3.0"],
            ["chore(ci): bump runner image"],
            _no_release("v0.3.0"),
            id="chore_with_other_scope_does_not_release",
        ),
        pytest.param(
            ["v0.3.0"],
            ["chore(deps-dev): update pytest"],
            _no_release("v0.3.0"),
            id="deps_dev_scope_does_not_release",
        ),
        pytest.param(
            ["v0.3.0"],
            ["chore(deps): update giskard-llm to v1.4.0"],
            _release("v0.3.1", "v0.3.0"),
            id="chore_deps_scope_releases_patch",
        ),
        pytest.param(
            ["v0.3.0"],
            ["build(deps): bump actions/checkout"],
            _release("v0.3.1", "v0.3.0"),
            id="build_deps_scope_releases_patch",
        ),
        pytest.param(
            ["v0.3.0"],
            ["fix(deps): update pydantic"],
            _release("v0.3.1", "v0.3.0"),
            id="fix_deps_scope_releases_patch",
        ),
        pytest.param(
            ["v0.3.0"],
            ["chore(deps): update lockfiles", "feat: add input"],
            _release("v0.4.0", "v0.3.0"),
            id="deps_with_feat_releases_minor",
        ),
        pytest.param(
            ["v1.2.3"],
            ["chore(deps): update lockfiles", "refactor!: drop input"],
            _release("v2.0.0", "v1.2.3"),
            id="deps_with_breaking_releases_major",
        ),
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


def _compute_with_requested_version(
    tmp_path: Path, repo_dir: Path, requested_version: str
) -> dict[str, str]:
    output_file = tmp_path / "github-output"
    rc = main(
        [
            "--github-output",
            str(output_file),
            "--compute-next-release-tag",
            "--repo-root",
            str(repo_dir),
            "--release-version",
            requested_version,
        ]
    )
    assert rc == 0
    return dict(
        line.split("=", 1) for line in output_file.read_text(encoding="utf-8").strip().splitlines()
    )


@pytest.mark.parametrize(
    ("initial_tags", "commits_after_tag", "requested_version", "expected_outputs"),
    [
        pytest.param(
            ["v0.4.0"],
            ["docs: polish"],
            "1.0.0",
            _release("v1.0.0", "v0.4.0"),
            id="releases_requested_version_without_releasable_commits",
        ),
        pytest.param(
            ["v0.4.0"],
            ["docs: polish"],
            "v1.0.0",
            _release("v1.0.0", "v0.4.0"),
            id="accepts_v_prefix",
        ),
        pytest.param(
            ["v0.4.0"],
            ["feat: add input"],
            "0.4.1",
            _release("v0.4.1", "v0.4.0"),
            id="requested_version_wins_over_computed_bump",
        ),
        pytest.param(
            [],
            [],
            "1.0.0",
            _release("v1.0.0", ""),
            id="requested_version_without_any_tag",
        ),
        pytest.param(
            ["v0.4.0"],
            ["feat: add input"],
            "  ",
            _release("v0.5.0", "v0.4.0"),
            id="blank_request_uses_computed_bump",
        ),
    ],
)
def test_compute_next_release_tag_with_requested_version(
    tmp_path: Path,
    initial_tags: list[str],
    commits_after_tag: list[str],
    requested_version: str,
    expected_outputs: dict[str, str],
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    for tag in initial_tags:
        subprocess.run(["git", "tag", tag], cwd=repo_dir, check=True)
    for msg in commits_after_tag:
        _commit_empty(repo_dir, msg)

    assert _compute_with_requested_version(tmp_path, repo_dir, requested_version) == (
        expected_outputs
    )


@pytest.mark.parametrize(
    ("requested_version", "error"),
    [
        pytest.param("1.0", r"'1\.0' is not a X\.Y\.Z version", id="rejects_missing_patch"),
        pytest.param("1.0.0-rc.1", r"is not a X\.Y\.Z version", id="rejects_prerelease_suffix"),
        pytest.param(
            "0.5.0", r"v0\.5\.0 must be higher than v0\.5\.0", id="rejects_existing_version"
        ),
        pytest.param(
            "0.4.0",
            r"v0\.4\.0 must be higher than v0\.5\.0",
            id="rejects_version_of_reachable_release",
        ),
        pytest.param(
            "0.4.5",
            r"v0\.4\.5 must be higher than v0\.5\.0",
            id="rejects_version_below_tag_on_another_branch",
        ),
    ],
)
def test_compute_next_release_tag_rejects_requested_version(
    tmp_path: Path, requested_version: str, error: str
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    subprocess.run(["git", "tag", "v0.4.0"], cwd=repo_dir, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=repo_dir, check=True)
    _commit_empty(repo_dir, "feat: released elsewhere")
    subprocess.run(["git", "tag", "v0.5.0"], cwd=repo_dir, check=True)
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo_dir, check=True)
    _commit_empty(repo_dir, "fix: on main")

    with pytest.raises(SystemExit, match=rf"::error::.*{error}"):
        _compute_with_requested_version(tmp_path, repo_dir, requested_version)


def test_compute_next_release_tag_refuses_requested_version_on_released_head(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    subprocess.run(["git", "tag", "v0.4.0"], cwd=repo_dir, check=True)

    with pytest.raises(SystemExit, match=r"::error::HEAD is already released as v0\.4\.0"):
        _compute_with_requested_version(tmp_path, repo_dir, "1.0.0")


def test_compute_next_release_tag_refuses_to_bootstrap_when_tags_are_unreachable(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=repo_dir, check=True)
    _commit_empty(repo_dir, "feat: released elsewhere")
    subprocess.run(["git", "tag", "v0.3.1"], cwd=repo_dir, check=True)
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo_dir, check=True)
    _commit_empty(repo_dir, "fix: unrelated")

    with pytest.raises(SystemExit, match=r"::error::No SemVer tag is reachable.*v0\.3\.1"):
        main(
            [
                "--github-output",
                str(tmp_path / "github-output"),
                "--compute-next-release-tag",
                "--repo-root",
                str(repo_dir),
            ]
        )
