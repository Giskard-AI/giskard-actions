"""Codex PR review helper subcommands used by the prepare, reviewer and aggregate jobs."""

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import codex_pr_assessment
import pytest

KEY = "azure-test-key-0123456789abcdef"
SPECIAL_KEY = "azure~~~test?key/0123456789abcdef~~~"
BUNDLED_SECURITY = codex_pr_assessment.strip_frontmatter(
    (codex_pr_assessment.BUNDLED_OVERLAYS_DIR / "security-auditor.md").read_text(encoding="utf-8")
)
GENERIC = (codex_pr_assessment.PROMPTS_DIR / "generic-reviewer.md").read_text(encoding="utf-8")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def stage(
    tmp_path: Path,
    reviewers: list[dict[str, str]],
    reviewer_map: dict[str, Any] | None = None,
) -> int:
    map_path = tmp_path / "reviewer-map.json"
    if reviewer_map is not None:
        codex_pr_assessment.write_json(map_path, {"rules": [{"name": "x"}], **reviewer_map})
    codex_pr_assessment.write_json(tmp_path / "reviewers.json", {"reviewers": reviewers})
    args = argparse.Namespace(
        reviewers=str(tmp_path / "reviewers.json"),
        trusted_config=str(tmp_path / "trusted"),
        reviewer_map=str(map_path),
        output_dir=str(tmp_path / "out"),
        head_sha="abc123",
        pr_number=7,
        expected_output=str(tmp_path / "expected.json"),
    )
    return codex_pr_assessment.main_stage_reviewers(args)


@pytest.mark.parametrize(
    ("files", "reviewer_map", "name", "instructions", "source"),
    [
        pytest.param(
            {".cursor/agents/clean-arch-guard.md": "Default dir persona.\n"},
            None,
            "clean-arch-guard",
            "Default dir persona.\n",
            "repository",
            id="default-agents-dir",
        ),
        pytest.param(
            {".github/agents/clean-arch-guard.md": "Custom dir persona.\n"},
            {"agents_dir": ".github/agents"},
            "clean-arch-guard",
            "Custom dir persona.\n",
            "repository",
            id="custom-agents-dir",
        ),
        pytest.param(
            {".github/agents/clean-arch-guard.agent.md": "Copilot persona.\n"},
            {"agents_dir": ".github/agents"},
            "clean-arch-guard",
            "Copilot persona.\n",
            "repository",
            id="copilot-agent-md",
        ),
        pytest.param(
            {
                ".cursor/agents/clean-arch-guard.md": "Plain persona.\n",
                ".cursor/agents/clean-arch-guard.agent.md": "Copilot persona.\n",
            },
            None,
            "clean-arch-guard",
            "Plain persona.\n",
            "repository",
            id="md-wins-over-agent-md",
        ),
        pytest.param(
            {".cursor/agents/clean-arch-guard.md": "---\nname: X\nmodel: y\n---\n\nBody only.\n"},
            None,
            "clean-arch-guard",
            "Body only.\n",
            "repository",
            id="frontmatter-stripped",
        ),
        pytest.param({}, None, "security-auditor", BUNDLED_SECURITY, "bundled", id="bundled"),
        pytest.param({}, None, "clean-arch-guard", GENERIC, "generic", id="generic"),
    ],
)
def test_stage_reviewers_loads_persona(
    tmp_path: Path,
    files: dict[str, str],
    reviewer_map: dict[str, Any] | None,
    name: str,
    instructions: str,
    source: str,
) -> None:
    for relative, text in files.items():
        write(tmp_path / "trusted" / relative, text)

    assert stage(tmp_path, [{"name": name, "role_references": ""}], reviewer_map) == 0

    staged = tmp_path / "out" / name
    assert (staged / "instructions.md").read_text(encoding="utf-8") == instructions
    reviewers = json.loads((tmp_path / "reviewers.json").read_text(encoding="utf-8"))
    assert reviewers["reviewers"][0]["persona_source"] == source


def test_stage_reviewers_writes_task_references_and_expected(tmp_path: Path) -> None:
    write(tmp_path / "trusted/.cursor/agents/security-auditor.md", "persona")
    write(tmp_path / "trusted/.cursor/agents/references/security.md", "patterns")
    write(tmp_path / "trusted/.cursor/agents/references/secret.md", "do not copy")

    status = stage(
        tmp_path,
        [{"name": "security-auditor", "role_references": "security.md"}],
    )

    assert status == 0
    staged = tmp_path / "out" / "security-auditor"
    prompts = codex_pr_assessment.PROMPTS_DIR
    assert (staged / "task.md").read_text(encoding="utf-8") == (
        (prompts / "pr-reviewer.md").read_text(encoding="utf-8")
        + "\n"
        + (prompts / "reviewer-pass-wrapper.md").read_text(encoding="utf-8")
        + "\nReviewer name: security-auditor\n"
    )
    assert sorted(path.name for path in (staged / "role-references").iterdir()) == ["security.md"]
    expected = json.loads((tmp_path / "expected.json").read_text(encoding="utf-8"))
    assert len(expected.pop("review_id")) == 32
    assert expected == {
        "head_sha": "abc123",
        "pr_number": 7,
        "reviewers": [{"name": "security-auditor", "persona_source": "repository"}],
    }


def test_stage_reviewers_reads_custom_references_dir(tmp_path: Path) -> None:
    write(tmp_path / "trusted/docs/review/arch.md", "architecture")

    status = stage(
        tmp_path,
        [{"name": "clean-arch-guard", "role_references": "arch.md"}],
        {"references_dir": "docs/review"},
    )

    assert status == 0
    staged = tmp_path / "out/clean-arch-guard/role-references/arch.md"
    assert staged.read_text(encoding="utf-8") == "architecture"


def test_stage_reviewers_warns_and_continues_on_missing_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "trusted/.cursor/agents/references/present.md", "present")

    status = stage(
        tmp_path,
        [{"name": "clean-arch-guard", "role_references": "missing.md,present.md"}],
    )

    assert status == 0
    assert "::warning::" in capsys.readouterr().out
    refs = tmp_path / "out/clean-arch-guard/role-references"
    assert sorted(path.name for path in refs.iterdir()) == ["present.md"]


def _symlinked_persona(trusted: Path) -> None:
    write(trusted / "outside.md", "elsewhere")
    (trusted / ".cursor/agents").mkdir(parents=True)
    (trusted / ".cursor/agents/clean-arch-guard.md").symlink_to(trusted / "outside.md")


def _symlinked_agents_dir(trusted: Path) -> None:
    write(trusted / "real-agents/clean-arch-guard.md", "persona")
    (trusted / ".cursor").mkdir(parents=True)
    (trusted / ".cursor/agents").symlink_to(trusted / "real-agents")


def _symlinked_reference(trusted: Path) -> None:
    write(trusted / "outside.md", "elsewhere")
    (trusted / ".cursor/agents/references").mkdir(parents=True)
    (trusted / ".cursor/agents/references/arch.md").symlink_to(trusted / "outside.md")


def _dangling_persona(trusted: Path) -> None:
    (trusted / ".cursor/agents").mkdir(parents=True)
    (trusted / ".cursor/agents/clean-arch-guard.md").symlink_to(trusted / "gone.md")


def _oversized_persona(trusted: Path) -> None:
    write(trusted / ".cursor/agents/clean-arch-guard.md", "x" * 30_001)


def _oversized_references(trusted: Path) -> None:
    for name in ("a.md", "b.md", "c.md"):
        write(trusted / ".cursor/agents/references" / name, "x" * 25_000)


def _non_utf8_persona(trusted: Path) -> None:
    path = trusted / ".cursor/agents/clean-arch-guard.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe persona")


def _directory_persona(trusted: Path) -> None:
    (trusted / ".cursor/agents/clean-arch-guard.md").mkdir(parents=True)


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(_symlinked_persona, id="symlinked-persona-file"),
        pytest.param(_symlinked_agents_dir, id="symlinked-agents-dir-component"),
        pytest.param(_symlinked_reference, id="symlinked-reference"),
        pytest.param(_dangling_persona, id="dangling-symlink-persona"),
        pytest.param(_oversized_persona, id="oversized-persona"),
        pytest.param(_oversized_references, id="oversized-references-total"),
        pytest.param(_non_utf8_persona, id="non-utf8-persona"),
        pytest.param(_directory_persona, id="persona-is-a-directory"),
    ],
)
def test_stage_reviewers_fails_on_tampered_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], setup: Any
) -> None:
    setup(tmp_path / "trusted")

    status = stage(
        tmp_path,
        [{"name": "clean-arch-guard", "role_references": "arch.md,a.md,b.md,c.md"}],
    )

    assert status == 1
    assert "::error::" in capsys.readouterr().err


@pytest.mark.parametrize(
    "reviewer_map",
    [
        pytest.param({"agents_dir": "../agents"}, id="dotdot-agents-dir"),
        pytest.param({"agents_dir": "/etc/agents"}, id="absolute-agents-dir"),
        pytest.param({"agents_dir": ".git/agents"}, id="git-segment"),
        pytest.param({"references_dir": "docs/.GIT"}, id="git-segment-any-case"),
        pytest.param({"agents_dir": "agénts"}, id="non-ascii-segment"),
    ],
)
def test_stage_reviewers_rejects_invalid_persona_dirs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], reviewer_map: dict[str, str]
) -> None:
    status = stage(tmp_path, [{"name": "clean-arch-guard", "role_references": ""}], reviewer_map)

    assert status == 1
    assert "::error::invalid config directory" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        pytest.param(".cursor/agents", True, id="default"),
        pytest.param("docs/ai_agents-v2.1", True, id="allowed-punctuation"),
        pytest.param("", False, id="empty"),
        pytest.param("a/./b", False, id="dot-segment"),
        pytest.param("a/../b", False, id="dotdot-segment"),
        pytest.param("a//b", False, id="empty-segment"),
        pytest.param("agents/", False, id="trailing-slash"),
        pytest.param("a\\b", False, id="backslash"),
        pytest.param("a b", False, id="space"),
        pytest.param(".Git", False, id="git-mixed-case"),
    ],
)
def test_validate_config_dir(value: str, valid: bool) -> None:
    if valid:
        assert codex_pr_assessment.validate_config_dir(value) == value
    else:
        with pytest.raises(ValueError, match="invalid config directory"):
            codex_pr_assessment.validate_config_dir(value)


@pytest.mark.parametrize(
    ("text", "body"),
    [
        pytest.param("---\na: b\n---\nBody\n", "Body\n", id="frontmatter"),
        pytest.param("\n\nBody\n", "Body\n", id="leading-blank-lines"),
        pytest.param("Body\n---\nmore\n", "Body\n---\nmore\n", id="rule-not-at-start"),
        pytest.param("---\nunterminated\n", "---\nunterminated\n", id="unterminated"),
    ],
)
def test_strip_frontmatter(text: str, body: str) -> None:
    assert codex_pr_assessment.strip_frontmatter(text) == body


def run_select(
    tmp_path: Path, files: list[str], reviewer_map: dict[str, Any] | None
) -> tuple[list[dict[str, str]], dict[str, str]]:
    context = write(
        tmp_path / "context.json", json.dumps({"files": [{"filename": f} for f in files]})
    )
    map_path = tmp_path / "map.json"
    if reviewer_map is not None:
        codex_pr_assessment.write_json(map_path, reviewer_map)
    github_output = tmp_path / "github-output"
    args = argparse.Namespace(
        context=str(context),
        output=str(tmp_path / "reviewers.json"),
        github_output=str(github_output),
        reviewer_map=str(map_path),
    )
    assert codex_pr_assessment.main_select(args) == 0
    outputs = dict(
        line.split("=", 1) for line in github_output.read_text(encoding="utf-8").splitlines()
    )
    reviewers = json.loads((tmp_path / "reviewers.json").read_text(encoding="utf-8"))
    return reviewers["reviewers"], outputs


def test_select_without_files_emits_empty_matrix(tmp_path: Path) -> None:
    reviewers, outputs = run_select(tmp_path, [], None)

    assert reviewers == []
    assert outputs["has_reviewers"] == "false"
    assert outputs["reviewer_count"] == "0"
    assert outputs["matrix"] == '{"include":[]}'
    assert set(outputs) == {
        "has_reviewers",
        "reviewer_count",
        "aggregator_model",
        "aggregator_effort",
        "matrix",
    }


def test_select_single_reviewer_from_map(tmp_path: Path) -> None:
    reviewer_map = {
        "rules": [{"name": "docs-reviewer", "path_prefixes": ["docs/"], "role_references": []}]
    }

    reviewers, outputs = run_select(tmp_path, ["docs/index.md"], reviewer_map)

    assert reviewers == [
        {
            "name": "docs-reviewer",
            "model_kind": "normal",
            "model": "gpt-5.6-luna",
            "effort": "medium",
            "role_references": "",
        }
    ]
    assert json.loads(outputs["matrix"]) == {"include": reviewers}
    assert outputs["reviewer_count"] == "1"


def test_select_caps_reviewers(tmp_path: Path) -> None:
    rules = [{"name": f"reviewer-{index:02d}", "path_prefixes": ["src/"]} for index in range(15)]

    reviewers, outputs = run_select(tmp_path, ["src/app.py"], {"rules": rules})

    assert [item["name"] for item in reviewers] == [f"reviewer-{index:02d}" for index in range(12)]
    assert outputs["reviewer_count"] == "12"


NORMAL_RULE = {"name": "sec", "path_prefixes": ["src/"], "role_references": ["a.md"]}
CRITICAL_RULE = {
    "name": "sec",
    "model_kind": "critical",
    "path_prefixes": ["src/"],
    "role_references": ["b.md", "a.md"],
}


@pytest.mark.parametrize(
    ("rules", "role_references"),
    [
        pytest.param([NORMAL_RULE, CRITICAL_RULE], "a.md,b.md", id="critical-upgrades-normal"),
        pytest.param([CRITICAL_RULE, NORMAL_RULE], "b.md,a.md", id="normal-never-downgrades"),
    ],
)
def test_select_critical_is_sticky_and_references_are_unioned(
    rules: list[dict[str, Any]], role_references: str
) -> None:
    reviewers = codex_pr_assessment.select_reviewers_from_map(["src/app.py"], rules)

    assert reviewers == [
        {
            "name": "sec",
            "model_kind": "critical",
            "model": "gpt-5.6-terra",
            "effort": "high",
            "role_references": role_references,
        }
    ]


@pytest.mark.parametrize(
    ("path", "selected"),
    [
        pytest.param("src/app.py", True, id="included-path"),
        pytest.param("src/generated/client.py", False, id="under-excluded-prefix"),
        pytest.param("src/generated", False, id="equals-exclude-without-slash"),
        pytest.param("src/generated_extra.py", True, id="sibling-of-excluded-prefix"),
    ],
)
def test_select_path_excludes(path: str, selected: bool) -> None:
    rules = [{"name": "py", "path_prefixes": ["src/"], "path_excludes": ["src/generated/"]}]

    reviewers = codex_pr_assessment.select_reviewers_from_map([path], rules)

    assert bool(reviewers) == selected


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param(
            [".github/workflows/ci.yml"],
            {"infra-reviewer": "normal", "security-auditor": "critical"},
            id="workflow-change",
        ),
        pytest.param(
            ["src/release_notes/prompts/synthesis.j2"],
            {"prompt-critic": "normal"},
            id="prompt-change",
        ),
        pytest.param(
            ["action.yaml"],
            {"infra-reviewer": "normal", "security-auditor": "critical"},
            id="root-action-yaml",
        ),
        pytest.param(["README.md"], {}, id="readme-only"),
    ],
)
def test_select_generic_defaults_without_map(
    tmp_path: Path, files: list[str], expected: dict[str, str]
) -> None:
    reviewers, _ = run_select(tmp_path, files, None)

    assert {item["name"]: item["model_kind"] for item in reviewers} == expected


def run_check_key_leak(monkeypatch: pytest.MonkeyPatch, root: Path, key: str | None) -> int:
    if key is None:
        monkeypatch.delenv("AZURE_AI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("AZURE_AI_API_KEY", key)
    monkeypatch.setattr(
        sys, "argv", ["codex_pr_assessment.py", "check-key-leak", "--path", str(root)]
    )
    return codex_pr_assessment.main()


@pytest.mark.parametrize(
    ("files", "expected_exit"),
    [
        pytest.param(
            {"reviewer-outputs/security.json": f'{{"summary": "{KEY}"}}'},
            1,
            id="key-in-reviewer-output-fails",
        ),
        pytest.param(
            {"final-review.md": f"## Review\n\n{KEY}\n"},
            1,
            id="key-in-final-review-fails",
        ),
        pytest.param(
            {"final-review.md": f"The proxy key is {KEY}, see above."},
            1,
            id="key-mid-line-fails",
        ),
        pytest.param(
            {"final-review.md": f"Only a prefix: {KEY[:12]}"},
            0,
            id="key-prefix-only-passes",
        ),
        pytest.param(
            {
                "reviewer-outputs/security.json": '{"summary": "No findings."}',
                "final-review.md": "## Review\n\nLooks good.\n",
            },
            0,
            id="clean-outputs-pass",
        ),
        pytest.param({}, 0, id="no-outputs-pass"),
    ],
)
def test_key_leak_check_blocks_outputs_containing_the_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    files: dict[str, str],
    expected_exit: int,
) -> None:
    for name, content in files.items():
        write(tmp_path / name, content)

    assert run_check_key_leak(monkeypatch, tmp_path, KEY) == expected_exit

    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    if expected_exit:
        assert "rotate AZURE_AI_API_KEY" in captured.err


def _b64(prefix: int, *, urlsafe: bool = False) -> str:
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return encode(b"p" * prefix + SPECIAL_KEY.encode() + b"tail").decode()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(SPECIAL_KEY, id="raw"),
        pytest.param(f"x {_b64(0)} y", id="base64-aligned"),
        pytest.param(f"x {_b64(1)} y", id="base64-offset-1"),
        pytest.param(f"x {_b64(2)} y", id="base64-offset-2"),
        pytest.param(f"x {_b64(0, urlsafe=True)} y", id="base64url-aligned"),
        pytest.param(f"x {_b64(1, urlsafe=True)} y", id="base64url-offset-1"),
        pytest.param(f"x {_b64(2, urlsafe=True)} y", id="base64url-offset-2"),
        pytest.param(f"x {SPECIAL_KEY.encode().hex()} y", id="hex-lower"),
        pytest.param(f"x {SPECIAL_KEY.encode().hex().upper()} y", id="hex-upper"),
        pytest.param(f"x {quote(SPECIAL_KEY, safe='')} y", id="url-encoded"),
        pytest.param(f"x {SPECIAL_KEY[::-1]} y", id="reversed"),
        pytest.param(f"{SPECIAL_KEY[:10]}\n  {SPECIAL_KEY[10:]}", id="split-by-whitespace"),
        pytest.param(
            "\n".join(_b64(1)[i : i + 8] for i in range(0, len(_b64(1)), 8)),
            id="wrapped-base64",
        ),
    ],
)
def test_key_leak_check_detects_encoded_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    content: str,
) -> None:
    write(tmp_path / "final-review.md", content)

    assert run_check_key_leak(monkeypatch, tmp_path, SPECIAL_KEY) == 1

    captured = capsys.readouterr()
    for needle in codex_pr_assessment.key_leak_needles(SPECIAL_KEY):
        assert needle.decode() not in captured.out + captured.err


def test_key_leak_check_passes_other_encoded_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    other = base64.b64encode(b"a different secret value entirely").decode()
    write(tmp_path / "final-review.md", f"{other}\n{other.encode().hex()}\n")

    assert run_check_key_leak(monkeypatch, tmp_path, SPECIAL_KEY) == 0


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(None, id="missing-key-fails-closed"),
        pytest.param("", id="empty-key-fails-closed"),
    ],
)
def test_key_leak_check_fails_closed_without_a_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str | None
) -> None:
    write(tmp_path / "final-review.md", "Looks good.\n")

    assert run_check_key_leak(monkeypatch, tmp_path, key) == 1


REVIEW_ID = "a" * 32
VALID_FINDING = {
    "category": "must_fix",
    "severity": "high",
    "path": "src/app.py",
    "line": 3,
    "title": "Bug",
    "rationale": "Breaks.",
    "suggested_action": "Fix it.",
}


def valid_output(name: str = "sec", **finding: Any) -> dict[str, Any]:
    return {"reviewer": name, "summary": "One issue.", "findings": [{**VALID_FINDING, **finding}]}


def run_collect(
    tmp_path: Path, artifacts: dict[str, dict[str, Any]], *, summary: bool = False
) -> int:
    downloaded = tmp_path / "downloaded"
    for name, files in artifacts.items():
        for filename, content in files.items():
            text = content if isinstance(content, str) else json.dumps(content)
            write(downloaded / name / filename, text)
    codex_pr_assessment.write_json(
        tmp_path / "expected.json",
        {
            "review_id": REVIEW_ID,
            "head_sha": "abc",
            "pr_number": 1,
            "reviewers": [{"name": "sec", "persona_source": "generic"}],
        },
    )
    args = argparse.Namespace(
        expected=str(tmp_path / "expected.json"),
        reviewers_dir=str(downloaded),
        output_dir=str(tmp_path / "outputs"),
        summary=str(tmp_path / "summary.md") if summary else None,
    )
    return codex_pr_assessment.main_collect_reviewer_outputs(args)


META = {"review_id": REVIEW_ID, "reviewer": "sec"}


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        pytest.param({"meta.json": META, "output.json": valid_output()}, None, id="valid"),
        pytest.param(
            {"meta.json": META, "output.json": valid_output(path=None, line=None)},
            None,
            id="valid-null-location",
        ),
        pytest.param({}, codex_pr_assessment.NO_OUTPUT_REASON, id="missing"),
        pytest.param(
            {"meta.json": META}, codex_pr_assessment.NO_OUTPUT_REASON, id="meta-without-output"
        ),
        pytest.param(
            {"meta.json": {**META, "review_id": "b" * 32}, "output.json": valid_output()},
            codex_pr_assessment.STALE_OUTPUT_REASON,
            id="stale-review-id",
        ),
        pytest.param(
            {"meta.json": {**META, "reviewer": "other"}, "output.json": valid_output()},
            codex_pr_assessment.STALE_OUTPUT_REASON,
            id="meta-reviewer-mismatch",
        ),
        pytest.param(
            {"meta.json": META, "output.json": valid_output("other")},
            codex_pr_assessment.INVALID_OUTPUT_REASON,
            id="output-reviewer-mismatch",
        ),
        pytest.param(
            {"meta.json": META, "output.json": valid_output(severity="urgent")},
            codex_pr_assessment.INVALID_OUTPUT_REASON,
            id="unknown-severity",
        ),
        pytest.param(
            {"meta.json": META, "output.json": valid_output(line=True)},
            codex_pr_assessment.INVALID_OUTPUT_REASON,
            id="bool-line",
        ),
        pytest.param(
            {"meta.json": META, "output.json": valid_output(extra="x")},
            codex_pr_assessment.INVALID_OUTPUT_REASON,
            id="extra-finding-key",
        ),
        pytest.param(
            {"meta.json": META, "output.json": {**valid_output(), "verdict": "ok"}},
            codex_pr_assessment.INVALID_OUTPUT_REASON,
            id="extra-top-level-key",
        ),
        pytest.param(
            {"meta.json": META, "output.json": "{not json"},
            codex_pr_assessment.INVALID_OUTPUT_REASON,
            id="invalid-json",
        ),
    ],
)
def test_collect_reviewer_outputs(
    tmp_path: Path, files: dict[str, Any], reason: str | None
) -> None:
    assert run_collect(tmp_path, {"sec": files}) == 0

    written = json.loads((tmp_path / "outputs/sec.json").read_text(encoding="utf-8"))
    if reason is None:
        assert written == files["output.json"]
    else:
        assert written["reviewer"] == "sec"
        (finding,) = written["findings"]
        assert finding["title"] == "Reviewer pass failed"
        assert (finding["category"], finding["severity"]) == ("informational", "info")
        assert (finding["path"], finding["line"]) == (None, None)
        assert reason in finding["rationale"]


def test_collect_reviewer_outputs_ignores_unexpected_reviewers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rogue = {"meta.json": {**META, "reviewer": "rogue"}, "output.json": valid_output("rogue")}

    assert run_collect(tmp_path, {"rogue": rogue}) == 0

    assert sorted(path.name for path in (tmp_path / "outputs").iterdir()) == ["sec.json"]
    assert "::notice::" in capsys.readouterr().out


def test_collect_reviewer_outputs_without_downloads_writes_placeholders(tmp_path: Path) -> None:
    assert run_collect(tmp_path, {}) == 0

    written = json.loads((tmp_path / "outputs/sec.json").read_text(encoding="utf-8"))
    assert written["findings"][0]["title"] == "Reviewer pass failed"


def test_collect_reviewer_outputs_appends_summary_table(tmp_path: Path) -> None:
    write(tmp_path / "summary.md", "# Earlier\n\n")

    assert run_collect(tmp_path, {}, summary=True) == 0

    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert summary.startswith("# Earlier\n\n")
    assert "| Reviewer | Result | Persona |" in summary
    assert f"| `sec` | placeholder: {codex_pr_assessment.NO_OUTPUT_REASON} | generic |" in summary


def run_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pr: dict[str, Any]
) -> dict[str, str]:
    def fake_gh_json(args: list[str], *, check: bool = True) -> Any:
        if args[1] == "repos/octo/app/pulls/5":
            return pr
        if args[1] == "repos/octo/app/commits/main":
            return {"sha": "cfg123"}
        raise AssertionError(args)

    monkeypatch.setattr(codex_pr_assessment, "gh_json", fake_gh_json)
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/app")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("CODEX_ASSESS_REQUIRE_SAME_REPO", "false")
    event = write(tmp_path / "event.json", json.dumps({"pull_request": {"number": 5}}))
    output = tmp_path / "github-output"
    args = argparse.Namespace(event_path=str(event), output=str(output))
    assert codex_pr_assessment.main_resolve(args) == 0
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def pr_payload(head_repo: str, state: str = "open") -> dict[str, Any]:
    return {
        "state": state,
        "head": {"sha": "head1", "repo": {"full_name": head_repo}},
        "base": {
            "sha": "base1",
            "repo": {"full_name": "octo/app", "default_branch": "main"},
        },
    }


@pytest.mark.parametrize(
    ("pr", "should_run"),
    [
        pytest.param(pr_payload("octo/app"), "true", id="same-repo-runs"),
        pytest.param(pr_payload("someone/fork"), "false", id="fork-never-runs-even-with-override"),
        pytest.param(pr_payload("octo/app", state="closed"), "false", id="closed-never-runs"),
    ],
)
def test_resolve_emits_default_branch_config_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pr: dict[str, Any], should_run: str
) -> None:
    outputs = run_resolve(monkeypatch, tmp_path, pr)

    assert outputs == {
        "should_run": should_run,
        "pr_number": "5",
        "head_sha": "head1",
        "base_sha": "base1",
        "config_sha": "cfg123",
    }


@pytest.mark.parametrize(
    ("prompt", "clause"),
    [
        pytest.param("pr-reviewer.md", "documents as accepted", id="reviewer-accepted-trade-off"),
        pytest.param("pr-reviewer.md", "manual or migration step", id="reviewer-human-step"),
        pytest.param(
            "pr-aggregator.md", "documents as accepted", id="aggregator-accepted-trade-off"
        ),
        pytest.param("pr-aggregator.md", "manual or migration step", id="aggregator-human-step"),
        pytest.param(
            "pr-aggregator.md",
            "Never link to local or runner paths",
            id="aggregator-no-local-links",
        ),
    ],
)
def test_review_prompts_keep_severity_and_path_rules(prompt: str, clause: str) -> None:
    text = (codex_pr_assessment.PROMPTS_DIR / prompt).read_text(encoding="utf-8")

    assert clause in text
