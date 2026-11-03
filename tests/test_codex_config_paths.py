"""Consumer config locations and bundled overlay fallbacks of the Codex PR review helper."""

import argparse
from pathlib import Path

import codex_pr_assessment
import pytest

NEW_MAP = ".github/giskard/codex-reviewer-map.json"
LEGACY_MAP = ".github/codex/reviewer-map.json"


@pytest.mark.parametrize(
    ("files", "expected", "warning"),
    [
        pytest.param([NEW_MAP], NEW_MAP, None, id="new_only"),
        pytest.param(
            [LEGACY_MAP],
            LEGACY_MAP,
            f"::warning::Reading legacy {LEGACY_MAP}; move it to {NEW_MAP}",
            id="legacy_only_warns_and_is_used",
        ),
        pytest.param(
            [NEW_MAP, LEGACY_MAP],
            NEW_MAP,
            f"::warning::Ignoring legacy {LEGACY_MAP} because {NEW_MAP} exists",
            id="both_prefers_new_and_warns",
        ),
        pytest.param([], None, None, id="neither_uses_defaults"),
    ],
)
def test_reviewer_map_location(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    files: list[str],
    expected: str | None,
    warning: str | None,
) -> None:
    for relative in files:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text('{"rules": []}', encoding="utf-8")

    resolved = codex_pr_assessment.resolve_reviewer_map_path(tmp_path / NEW_MAP)

    assert resolved == (tmp_path / expected if expected else None)
    stderr = capsys.readouterr().err
    if warning is None:
        assert "::warning::" not in stderr
    else:
        assert warning in stderr
    if expected == LEGACY_MAP:
        assert "v1.0.0" in stderr


def _stage(tmp_path: Path, name: str) -> tuple[int, Path]:
    output = tmp_path / "out" / "reviewer-instructions.md"
    args = argparse.Namespace(
        name=name,
        trusted_config=str(tmp_path / "trusted"),
        output=str(output),
        references_output=str(tmp_path / "out" / "refs"),
        role_references="",
    )
    return codex_pr_assessment.main_stage_overlay(args), output


@pytest.mark.parametrize(
    "name", ["security-auditor", "infra-reviewer", "prompt-critic"], ids=lambda n: f"bundled-{n}"
)
def test_bundled_overlay_is_used_without_consumer_persona(tmp_path: Path, name: str) -> None:
    status, output = _stage(tmp_path, name)

    assert status == 0
    bundled = codex_pr_assessment.BUNDLED_OVERLAYS_DIR / f"{name}.md"
    assert output.read_text(encoding="utf-8") == bundled.read_text(encoding="utf-8")


def test_consumer_persona_wins_over_bundled_overlay(tmp_path: Path) -> None:
    persona = tmp_path / "trusted" / ".cursor" / "agents" / "security-auditor.md"
    persona.parent.mkdir(parents=True)
    persona.write_text("consumer persona", encoding="utf-8")

    status, output = _stage(tmp_path, "security-auditor")

    assert status == 0
    assert output.read_text(encoding="utf-8") == "consumer persona"


def test_unknown_reviewer_without_persona_fails(tmp_path: Path) -> None:
    status, _ = _stage(tmp_path, "clean-arch-guard")

    assert status == 1


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
