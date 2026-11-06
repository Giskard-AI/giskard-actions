"""Consumer config locations and bundled overlay fallbacks of the Codex PR review helper."""

import argparse
from pathlib import Path

import codex_pr_assessment
import pytest

NEW_MAP = ".github/giskard/codex-reviewer-map.json"
V0_3_MAP = ".github/codex/reviewer-map.json"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param([NEW_MAP], NEW_MAP, id="new_path_is_used"),
        pytest.param([V0_3_MAP], None, id="v0_3_path_is_ignored"),
        pytest.param([], None, id="no_map_uses_defaults"),
    ],
)
def test_reviewer_map_location(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    files: list[str],
    expected: str | None,
) -> None:
    for relative in files:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text('{"rules": []}', encoding="utf-8")

    resolved = codex_pr_assessment.resolve_reviewer_map_path(tmp_path / NEW_MAP)

    assert resolved == (tmp_path / expected if expected else None)
    assert "::warning::" not in capsys.readouterr().err


def _stage(tmp_path: Path, name: str) -> tuple[int, Path]:
    reviewers = tmp_path / "reviewers.json"
    codex_pr_assessment.write_json(
        reviewers, {"reviewers": [{"name": name, "role_references": ""}]}
    )
    args = argparse.Namespace(
        reviewers=str(reviewers),
        trusted_config=str(tmp_path / "trusted"),
        reviewer_map=str(tmp_path / "missing-map.json"),
        output_dir=str(tmp_path / "out"),
        head_sha="abc123",
        pr_number=7,
        expected_output=str(tmp_path / "expected.json"),
    )
    return codex_pr_assessment.main_stage_reviewers(args), tmp_path / "out" / name


@pytest.mark.parametrize(
    "name", ["security-auditor", "infra-reviewer", "prompt-critic"], ids=lambda n: f"bundled-{n}"
)
def test_bundled_overlay_is_used_without_consumer_persona(tmp_path: Path, name: str) -> None:
    status, staged = _stage(tmp_path, name)

    assert status == 0
    bundled = codex_pr_assessment.BUNDLED_OVERLAYS_DIR / f"{name}.md"
    assert (staged / "instructions.md").read_text(
        encoding="utf-8"
    ) == codex_pr_assessment.strip_frontmatter(bundled.read_text(encoding="utf-8"))


def test_consumer_persona_wins_over_bundled_overlay(tmp_path: Path) -> None:
    persona = tmp_path / "trusted" / ".cursor" / "agents" / "security-auditor.md"
    persona.parent.mkdir(parents=True)
    persona.write_text("consumer persona", encoding="utf-8")

    status, staged = _stage(tmp_path, "security-auditor")

    assert status == 0
    assert (staged / "instructions.md").read_text(encoding="utf-8") == "consumer persona"


def test_unknown_reviewer_without_persona_uses_generic_prompt(tmp_path: Path) -> None:
    status, staged = _stage(tmp_path, "clean-arch-guard")

    assert status == 0
    generic = codex_pr_assessment.PROMPTS_DIR / "generic-reviewer.md"
    assert (staged / "instructions.md").read_text(encoding="utf-8") == generic.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "forbidden",
    ["llm-hub", "Linear", "release-notes CLI", "self-test", "Giskard"],
    ids=lambda word: f"no-{word.lower().replace(' ', '-')}",
)
def test_bundled_overlays_are_repository_agnostic(forbidden: str) -> None:
    for overlay in codex_pr_assessment.BUNDLED_OVERLAYS_DIR.glob("*.md"):
        assert forbidden not in overlay.read_text(encoding="utf-8"), overlay.name


@pytest.mark.parametrize(
    ("overlay", "rule"),
    [
        pytest.param(
            "security-auditor.md",
            "Passing a secret to a declared action input (`with:`) or a reusable workflow's "
            "`secrets:` is fine.",
            id="security-allows-secret-action-inputs",
        ),
        pytest.param(
            "infra-reviewer.md",
            "Same-repo references (`./path`, `$/path`) are valid and are not pinned.",
            id="infra-allows-same-repo-references",
        ),
    ],
)
def test_overlay_rules_match_this_repository_personas(overlay: str, rule: str) -> None:
    repo_root = Path(codex_pr_assessment.__file__).resolve().parents[2]

    assert rule in (codex_pr_assessment.BUNDLED_OVERLAYS_DIR / overlay).read_text(encoding="utf-8")
    assert rule in (repo_root / ".cursor" / "agents" / overlay).read_text(encoding="utf-8")
