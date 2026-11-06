"""Codex review hardening: the Codex User on every Codex step, and the Key Leak Check."""

import sys
from pathlib import Path

import codex_pr_assessment
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_ACTION = REPO_ROOT / ".github/actions/codex-pr-review/action.yml"
KEY = "azure-test-key-0123456789abcdef"


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
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert run_check_key_leak(monkeypatch, tmp_path, KEY) == expected_exit

    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    if expected_exit:
        assert "rotate AZURE_AI_API_KEY" in captured.err


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
    (tmp_path / "final-review.md").write_text("Looks good.\n", encoding="utf-8")

    assert run_check_key_leak(monkeypatch, tmp_path, key) == 1


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def codex_action_steps() -> list[tuple[str, dict[str, str]]]:
    """Each `openai/codex-action` step as (file:step name, its `with:` inputs)."""
    cases = []
    yaml_files = sorted(REPO_ROOT.glob(".github/**/*.yml")) + sorted(
        REPO_ROOT.glob(".github/**/*.yaml")
    )
    for path in yaml_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "uses: openai/codex-action@" not in line:
                continue
            step_indent = indent_of(line)
            name = next(
                (
                    candidate.split("name:", 1)[1].strip()
                    for candidate in reversed(lines[:index])
                    if candidate.lstrip().startswith("- name:")
                ),
                f"line {index + 1}",
            )
            inputs: dict[str, str] = {}
            in_with = False
            for following in lines[index + 1 :]:
                if following.strip() and indent_of(following) < step_indent:
                    break
                if indent_of(following) == step_indent:
                    in_with = following.strip() == "with:"
                    continue
                if in_with and indent_of(following) == step_indent + 2 and ":" in following:
                    key, value = following.strip().split(":", 1)
                    inputs[key] = value.strip()
            cases.append((f"{path.relative_to(REPO_ROOT)}:{name}", inputs))
    return cases


CODEX_ACTION_STEPS = [pytest.param(inputs, id=step_id) for step_id, inputs in codex_action_steps()]


def test_review_action_runs_every_codex_pass() -> None:
    assert len(CODEX_ACTION_STEPS) == 13


@pytest.mark.parametrize("inputs", CODEX_ACTION_STEPS)
def test_codex_action_steps_run_as_the_codex_user(inputs: dict[str, str]) -> None:
    assert inputs.get("safety-strategy") == "unprivileged-user"
    assert inputs.get("codex-user") == "codex"
    assert inputs.get("permission-profile") == '":read-only"'
    assert "sandbox" not in inputs


def test_key_leak_check_runs_between_the_aggregator_and_the_upload() -> None:
    review = REVIEW_ACTION.read_text(encoding="utf-8")
    aggregator = review.index("- name: Run Codex code-review aggregator")
    check = review.index("- name: Check Codex output for the API key")
    upload = review.index("- name: Upload Codex review artifacts")
    assert aggregator < check < upload
    assert "check-key-leak --path assessment" in review[check:upload]


def test_codex_user_is_created_before_the_first_codex_pass() -> None:
    review = REVIEW_ACTION.read_text(encoding="utf-8")
    create = review.index("- name: Create Codex User")
    assert create < review.index("uses: openai/codex-action@")
    assert "sudo rm -f /home/codex/.codex/config.toml" in review
    assert '"${HOME}/.codex/config.toml"' not in review
