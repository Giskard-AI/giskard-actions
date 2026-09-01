"""Tests for GitHub Actions output sanitization."""

import json
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
