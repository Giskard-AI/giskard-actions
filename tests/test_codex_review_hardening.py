"""Codex review hardening: job split, drop-sudo per Codex pass, Key Leak Check before uploads."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_WORKFLOW = REPO_ROOT / ".github/workflows/codex-pr-review.yml"
PREPARE_ACTION = REPO_ROOT / ".github/actions/codex-review-prepare/action.yml"
PASS_ACTION = REPO_ROOT / ".github/actions/codex-review-pass/action.yml"
CODEX_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*openai/codex-action@", re.MULTILINE)
UPLOAD_USES_RE = re.compile(r"uses:\s*actions/upload-artifact@")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def blocks_under(text: str, header: str, indent: int) -> dict[str, str]:
    """Split the mapping under `header:` into {key: block text} for keys at `indent`."""
    lines = text.splitlines()
    start = lines.index(header) + 1
    blocks: dict[str, list[str]] = {}
    current = None
    for line in lines[start:]:
        if line.strip() and indent_of(line) < indent:
            break
        if line.strip() and indent_of(line) == indent and line.rstrip().endswith(":"):
            current = line.strip()[:-1]
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return {key: "\n".join(body) for key, body in blocks.items()}


def steps_of(text: str) -> list[str]:
    """Split a composite action's `steps:` list into one text block per step."""
    body = "\n" + text.split("\n  steps:\n", 1)[1]
    return [f"- name:{chunk}" for chunk in body.split("\n    - name:")[1:]]


def step_named(steps: list[str], name: str) -> int:
    return next(i for i, step in enumerate(steps) if step.startswith(f"- name: {name}\n"))


JOBS = blocks_under(REVIEW_WORKFLOW.read_text(encoding="utf-8"), "jobs:", 2)
PASS_STEPS = steps_of(PASS_ACTION.read_text(encoding="utf-8"))


def test_review_workflow_has_the_four_jobs() -> None:
    assert list(JOBS) == ["prepare", "reviewer", "aggregate", "post"]


@pytest.mark.parametrize(
    ("job", "permissions"),
    [
        pytest.param(
            "prepare",
            {"contents: read", "issues: read", "pull-requests: read"},
            id="prepare-reads-github-only",
        ),
        pytest.param("reviewer", {"contents: read"}, id="reviewer-has-no-github-write"),
        pytest.param("aggregate", {"contents: read"}, id="aggregate-has-no-github-write"),
        pytest.param("post", {"contents: read", "pull-requests: write"}, id="post-only-writes"),
    ],
)
def test_job_permissions(job: str, permissions: set[str]) -> None:
    lines = JOBS[job].split("    permissions:\n", 1)[1].splitlines()
    stop = next(
        (i for i, line in enumerate(lines) if line.strip() and indent_of(line) < 6), len(lines)
    )
    granted = {line.split("#", 1)[0].strip() for line in lines[:stop] if line.strip()}
    assert granted == permissions


@pytest.mark.parametrize(
    ("job", "holds_key"),
    [
        pytest.param("prepare", False, id="prepare-has-no-key"),
        pytest.param("reviewer", True, id="reviewer-has-key"),
        pytest.param("aggregate", True, id="aggregate-has-key"),
        pytest.param("post", False, id="post-has-no-key"),
    ],
)
def test_only_codex_jobs_receive_the_key_and_environment(job: str, holds_key: bool) -> None:
    block = JOBS[job]
    assert ("secrets.AZURE_AI_API_KEY" in block) == holds_key
    assert ("environment:" in block) == holds_key


@pytest.mark.parametrize("job", list(JOBS), ids=lambda job: f"{job}-has-timeout")
def test_every_job_has_a_timeout(job: str) -> None:
    assert "    timeout-minutes:" in JOBS[job]


@pytest.mark.parametrize(
    ("job", "snippet"),
    [
        pytest.param(
            "reviewer",
            "matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}",
            id="matrix-from-prepare",
        ),
        pytest.param(
            "reviewer", "max-parallel: ${{ inputs.max_parallel }}", id="max-parallel-from-input"
        ),
        pytest.param("reviewer", "fail-fast: false", id="reviewers-do-not-cancel-each-other"),
        pytest.param(
            "reviewer",
            "needs.prepare.outputs.has_reviewers == 'true'",
            id="empty-matrix-guarded",
        ),
        pytest.param("aggregate", "needs: [prepare, reviewer]", id="aggregate-after-reviewers"),
        pytest.param("aggregate", "!cancelled()", id="aggregate-skipped-on-cancel"),
        pytest.param(
            "aggregate",
            "needs.prepare.result == 'success'",
            id="aggregate-needs-successful-prepare",
        ),
        pytest.param("post", "needs: [prepare, aggregate]", id="post-reads-prepare-outputs"),
        pytest.param(
            "post", "needs.aggregate.result == 'success'", id="post-needs-successful-aggregate"
        ),
    ],
)
def test_job_graph(job: str, snippet: str) -> None:
    assert snippet in JOBS[job]


def test_no_job_runs_after_cancellation() -> None:
    assert "always()" not in REVIEW_WORKFLOW.read_text(encoding="utf-8")


def test_workflow_outputs_come_from_prepare() -> None:
    workflow = REVIEW_WORKFLOW.read_text(encoding="utf-8")
    assert "value: ${{ jobs.prepare.outputs.should_run }}" in workflow
    assert "value: ${{ jobs.prepare.outputs.pr_number }}" in workflow


@pytest.mark.parametrize(
    ("job", "passes"),
    [
        pytest.param("prepare", 0, id="prepare-runs-no-codex"),
        pytest.param("reviewer", 1, id="reviewer-runs-one-pass"),
        pytest.param("aggregate", 1, id="aggregate-runs-one-pass"),
        pytest.param("post", 0, id="post-runs-no-codex"),
    ],
)
def test_each_job_runs_at_most_one_codex_pass(job: str, passes: int) -> None:
    assert JOBS[job].count("uses: $/.github/actions/codex-review-pass") == passes


def test_only_the_pass_action_runs_codex() -> None:
    files = sorted(REPO_ROOT.glob(".github/**/*.yml")) + sorted(REPO_ROOT.glob(".github/**/*.yaml"))
    runners = {
        str(path.relative_to(REPO_ROOT)): len(CODEX_USES_RE.findall(path.read_text("utf-8")))
        for path in files
        if CODEX_USES_RE.search(path.read_text("utf-8"))
    }
    assert runners == {".github/actions/codex-review-pass/action.yml": 1}


@pytest.mark.parametrize(
    "setting",
    [
        pytest.param("safety-strategy: drop-sudo", id="drop-sudo"),
        pytest.param('permission-profile: ":read-only"', id="read-only-profile"),
    ],
)
def test_codex_pass_is_sandboxed(setting: str) -> None:
    run_codex = PASS_STEPS[step_named(PASS_STEPS, "Run Codex")]
    assert setting in run_codex
    assert "\n        sandbox:" not in run_codex


def test_codex_pass_disables_web_search() -> None:
    args = PASS_STEPS[step_named(PASS_STEPS, "Set Codex arguments")]
    assert "'web_search=\"disabled\"'" in args


def test_sudo_is_only_used_before_codex_drops_it() -> None:
    run_codex = step_named(PASS_STEPS, "Run Codex")
    assert any("sudo " in step for step in PASS_STEPS[:run_codex])
    assert not any("sudo " in step for step in PASS_STEPS[run_codex + 1 :])


def test_uploads_follow_a_passing_key_leak_check() -> None:
    check = step_named(PASS_STEPS, "Key Leak Check")
    uploads = [i for i, step in enumerate(PASS_STEPS) if UPLOAD_USES_RE.search(step)]
    assert uploads
    assert all(i > check for i in uploads)
    for step in PASS_STEPS[check + 1 :]:
        condition = next((line for line in step.splitlines() if line.strip().startswith("if:")), "")
        assert not re.search(r"always\(\)|failure\(\)|cancelled\(\)", condition)
        assert "continue-on-error" not in step
    assert "check-key-leak --path review-pass --path assessment" in PASS_STEPS[check]


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PREPARE_ACTION, id="prepare-action"),
        pytest.param(PASS_ACTION, id="pass-action"),
    ],
)
def test_uploads_overwrite_earlier_attempts(path: Path) -> None:
    steps = steps_of(path.read_text(encoding="utf-8"))
    uploads = [step for step in steps if UPLOAD_USES_RE.search(step)]
    assert uploads
    for step in uploads:
        assert "overwrite: true" in step
        assert "${{ inputs.pr_number }}" in step or "steps.resolve.outputs.pr_number" in step


def test_codex_failures_fail_the_reviewer_job() -> None:
    for name in ("Run Codex", "Check Codex output"):
        assert "continue-on-error" not in PASS_STEPS[step_named(PASS_STEPS, name)]


def test_prepare_holds_no_key_and_reads_config_from_the_default_branch() -> None:
    prepare = PREPARE_ACTION.read_text(encoding="utf-8")
    assert "azure_ai_api_key" not in prepare
    assert "ref: ${{ steps.resolve.outputs.config_sha }}" in prepare
    assert "base_sha" not in prepare


def test_same_repo_check_cannot_be_turned_off() -> None:
    files = [PREPARE_ACTION, PASS_ACTION, REVIEW_WORKFLOW]
    assert not any("CODEX_ASSESS_REQUIRE_SAME_REPO" in path.read_text("utf-8") for path in files)


def test_prepare_refuses_self_hosted_runners_unless_allowed() -> None:
    steps = steps_of(PREPARE_ACTION.read_text(encoding="utf-8"))
    guard = steps[0]
    assert guard.startswith("- name: Require a GitHub-hosted runner\n")
    assert "if: inputs.allow_self_hosted != 'true'" in guard
    assert '"${RUNNER_ENVIRONMENT:-}" != "github-hosted"' in guard


def test_sequential_review_action_is_gone() -> None:
    assert not (REPO_ROOT / ".github/actions/codex-pr-review").exists()
