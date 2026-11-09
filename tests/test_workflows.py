"""Static security checks on .github/workflows/*.yml, parsed line by line (no YAML dependency)."""

import json
import re
import socket
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
WORKFLOW_FILES = sorted(WORKFLOWS_DIR.glob("*.yml"))
SHARED_WORKFLOW_FILES = [
    WORKFLOWS_DIR / f"{name}.yml" for name in ("python-ci", "ts-ci", "zizmor-scan")
]

USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<comment>.*)$")
RUN_RE = re.compile(r"^\s*(?:-\s+)?run:\s*(?P<value>.*)$")
SHA_PIN_RE = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT_RE = re.compile(r"^\s*#\s*v\d+\.\d+\.\d+")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _children(lines: list[str], start: int, parent_indent: int) -> list[str]:
    """Lines after ``start`` until the first non-blank line indented at most ``parent_indent``."""
    block = []
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= parent_indent:
            break
        block.append(line)
    return block


def run_scripts(text: str) -> list[str]:
    lines = text.splitlines()
    scripts = []
    for i, line in enumerate(lines):
        match = RUN_RE.match(line)
        if not match:
            continue
        value = match["value"]
        if value.startswith(("|", ">")):
            scripts.append("\n".join(_children(lines, i, line.index("run:"))))
        else:
            scripts.append(value)
    return scripts


def action_refs(text: str) -> list[tuple[str, str]]:
    return [(m["ref"], m["comment"]) for line in text.splitlines() if (m := USES_RE.match(line))]


def checkout_steps(text: str) -> list[str]:
    lines = text.splitlines()
    steps = []
    for i, line in enumerate(lines):
        match = USES_RE.match(line)
        if not match or not match["ref"].startswith("actions/checkout@"):
            continue
        start = next(j for j in range(i, -1, -1) if lines[j].lstrip().startswith("- "))
        steps.append("\n".join([lines[start], *_children(lines, start, _indent(lines[start]))]))
    return steps


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            "- run: echo ${{ inputs.cmd }}\n",
            ["echo ${{ inputs.cmd }}"],
            id="inline-run-keeps-expression",
        ),
        pytest.param(
            "- run: |\n    echo ok\n    echo ${{ inputs.cmd }}\n  env:\n    A: b\n",
            ["    echo ok\n    echo ${{ inputs.cmd }}"],
            id="block-run-stops-at-sibling-key",
        ),
        pytest.param(
            '- env:\n    CMD: ${{ inputs.cmd }}\n  run: bash -c "$CMD"\n',
            ['bash -c "$CMD"'],
            id="expression-in-env-is-not-run",
        ),
        pytest.param(
            "defaults:\n  run:\n    working-directory: ${{ inputs.dir }}\n",
            [""],
            id="defaults-run-mapping-is-not-a-script",
        ),
    ],
)
def test_run_scripts(snippet: str, expected: list[str]):
    assert run_scripts(snippet) == expected


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            "- name: Checkout\n  uses: actions/checkout@abc # v1.0.0\n  with:\n"
            "    persist-credentials: false\n- run: echo\n",
            True,
            id="checkout-with-persist-credentials-false",
        ),
        pytest.param(
            "- uses: actions/checkout@abc # v1.0.0\n- with:\n    persist-credentials: false\n",
            False,
            id="persist-credentials-on-next-step-does-not-count",
        ),
    ],
)
def test_checkout_steps(snippet: str, expected: bool):
    (step,) = checkout_steps(snippet)
    assert ("persist-credentials: false" in step) is expected


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.stem)
def test_top_level_permissions_are_empty(path: Path):
    assert re.search(r"^permissions: \{\}\s*$", path.read_text(), re.MULTILINE)


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.stem)
def test_third_party_actions_are_sha_pinned(path: Path):
    for ref, comment in action_refs(path.read_text()):
        if ref.startswith(("./", "$/")):
            continue
        assert SHA_PIN_RE.match(ref), f"{ref} is not pinned to a full commit SHA"
        assert VERSION_COMMENT_RE.match(comment), f"{ref} has no `# vX.Y.Z` comment"


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.stem)
def test_checkout_does_not_persist_credentials(path: Path):
    for step in checkout_steps(path.read_text()):
        assert "persist-credentials: false" in step, step


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.stem)
def test_run_scripts_have_no_expressions(path: Path):
    for script in run_scripts(path.read_text()):
        assert "${{" not in script, script


def job_blocks(text: str) -> dict[str, str]:
    lines = text.splitlines()
    start = lines.index("jobs:")
    return {
        line.strip().rstrip(":"): "\n".join(_children(lines, i, 2))
        for i, line in enumerate(lines[start + 1 :], start + 1)
        if re.match(r"^  [\w-]+:$", line)
    }


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            "jobs:\n  a:\n    timeout-minutes: 5\n  b:\n    runs-on: x\n",
            {"a": "    timeout-minutes: 5", "b": "    runs-on: x"},
            id="one-block-per-job",
        ),
    ],
)
def test_job_blocks(snippet: str, expected: dict[str, str]):
    assert job_blocks(snippet) == expected


@pytest.mark.parametrize("path", SHARED_WORKFLOW_FILES, ids=lambda p: p.stem)
def test_shared_workflow_jobs_set_timeout(path: Path):
    jobs = job_blocks(path.read_text())
    assert jobs
    for name, block in jobs.items():
        assert re.search(r"^    timeout-minutes: ", block, re.MULTILINE), f"job {name}"


def input_default(text: str, name: str) -> str:
    lines = text.splitlines()
    start = lines.index(f"      {name}:")
    (default,) = [
        line for line in _children(lines, start, 6) if line.startswith("        default:")
    ]
    return default.split(":", 1)[1].strip()


@pytest.mark.parametrize(
    ("workflow", "cache_input", "off_value", "step_line"),
    [
        pytest.param(
            "python-ci",
            "enable-cache",
            "false",
            "enable-cache: ${{ inputs.enable-cache }}",
            id="python-ci-setup-uv-cache-off",
        ),
        pytest.param(
            "ts-ci",
            "cache",
            '""',
            "cache: ${{ inputs.cache }}",
            id="ts-ci-setup-node-cache-off",
        ),
    ],
)
def test_dependency_cache_is_off_by_default(
    workflow: str, cache_input: str, off_value: str, step_line: str
):
    text = (WORKFLOWS_DIR / f"{workflow}.yml").read_text()
    assert input_default(text, cache_input) == off_value
    assert text.count(f"          {step_line}\n") == len(job_blocks(text))


@pytest.mark.parametrize(
    ("workflow", "name", "expected"),
    [
        pytest.param("python-ci", "uv-version", '""', id="uv-from-project-or-latest"),
        pytest.param("python-ci", "python-version", '""', id="python-from-project"),
        pytest.param("ts-ci", "node-version", '"26"', id="node-active-lts"),
    ],
)
def test_tool_version_defaults(workflow: str, name: str, expected: str):
    assert input_default((WORKFLOWS_DIR / f"{workflow}.yml").read_text(), name) == expected


ROOT = WORKFLOWS_DIR.parents[1]
SCAN_SH = ROOT / "scripts" / "zizmor" / "scan.sh"
ZIZMOR_IMAGE_FILES = [WORKFLOWS_DIR / "zizmor.yml", SCAN_SH]
ZIZMOR_IMAGE_RE = re.compile(
    r"^\s*ZIZMOR_IMAGE(?:: |=\")ghcr\.io/zizmorcore/zizmor:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}\"?$",
    re.MULTILINE,
)
ZIZMOR_IMAGE_TAG_RE = re.compile(r"zizmor:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")


@pytest.mark.parametrize("path", ZIZMOR_IMAGE_FILES, ids=lambda p: p.name)
def test_zizmor_image_is_digest_pinned(path: Path):
    text = path.read_text()
    assert len(ZIZMOR_IMAGE_RE.findall(text)) == 1
    assert "zizmorcore/zizmor-action" not in text


def test_zizmor_scans_share_one_image():
    images = {ZIZMOR_IMAGE_TAG_RE.search(path.read_text())[0] for path in ZIZMOR_IMAGE_FILES}
    assert len(images) == 1


def test_gate_workflow_has_no_image_of_its_own():
    assert "ZIZMOR_IMAGE" not in (WORKFLOWS_DIR / "zizmor-scan.yml").read_text()


@pytest.mark.parametrize("path", ZIZMOR_IMAGE_FILES, ids=lambda p: p.name)
def test_renovate_tracks_zizmor_image(path: Path):
    config = json.loads((ROOT / "renovate.json").read_text())
    (manager,) = [m for m in config["customManagers"] if m.get("datasourceTemplate") == "docker"]
    assert any(
        re.search(p.strip("/"), path.relative_to(ROOT).as_posix())
        for p in manager["managerFilePatterns"]
    )
    (pattern,) = manager["matchStrings"]
    match = re.search(pattern.replace("(?<", "(?P<"), path.read_text())
    assert match and match["currentDigest"].startswith("sha256:")


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param("--collect=all", id="audits-gitignored-files"),
        pytest.param("--strict-collection", id="fails-on-unparsable-files"),
        pytest.param("--persona=auditor", id="runs-the-auditor-persona"),
        pytest.param("--offline", id="runs-offline"),
        pytest.param("--network none", id="container-has-no-network"),
        pytest.param('--volume "${src}:/src:ro"', id="pull-request-mounted-read-only"),
        pytest.param("--config=/config/zizmor.yml", id="config-from-outside-the-checkout"),
    ],
)
def test_zizmor_scan_flags(flag: str):
    assert flag in SCAN_SH.read_text()


def test_zizmor_scan_honours_inline_ignores():
    assert "--no-ignores" not in SCAN_SH.read_text()


def test_gate_annotates_findings():
    (job,) = [b for name, b in job_blocks(gate_text()).items() if name == "zizmor"]
    assert "ZIZMOR_FORMAT: github" in job
    assert "bash gate/scripts/zizmor/scan.sh src base" in job


def test_gate_scripts_come_from_the_running_workflow_commit():
    text = gate_text()
    assert text.count("repository: Giskard-AI/giskard-actions") == 2
    assert text.count("ref: ${{ job.workflow_sha }}") == 2


@pytest.mark.parametrize("job", ["zizmor", "zizmor-pins"])
def test_gate_commit_is_checked_before_its_checkout(job: str):
    block = job_blocks(gate_text())[job]
    guard = block.index("- name: Check the Gate commit")
    assert guard < block.index("- name: Checkout the Gate scripts")
    assert "WORKFLOW_SHA: ${{ job.workflow_sha }}" in block
    assert '"${WORKFLOW_SHA}" =~ ^[0-9a-f]{40}$' in block
    assert '"${WORKFLOW_REPOSITORY}" != Giskard-AI/giskard-actions' in block


def test_pins_job_wiring():
    block = job_blocks(gate_text())["zizmor-pins"]
    assert re.search(r"permissions:\n\s+contents: read\n\s+env:", block)
    assert "fetch-depth: 2" in block
    assert "GH_TOKEN: ${{ github.token }}" in block
    assert "python3 gate/scripts/zizmor/pins.py src base" in block
    assert "github.event.pull_request.base.sha" in block


def test_gate_does_not_cancel_in_progress_runs():
    assert not re.search(r"^\s+cancel-in-progress:", gate_text(), re.MULTILINE)


def gate_text() -> str:
    return (WORKFLOWS_DIR / "zizmor-scan.yml").read_text()


def gate_script(marker: str) -> str:
    (script,) = [s for s in run_scripts(gate_text()) if marker in s]
    return script


def fake_uvx(tmp_path: Path, status: int) -> dict[str, str]:
    """PATH with a uvx that records its arguments and the config it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "uvx").write_text(
        "#!/bin/bash\n"
        f'echo "$@" > {tmp_path}/args\n'
        'for a in "$@"; do\n'
        f'  [[ $a == --config=* ]] && cp "${{a#--config=}}" {tmp_path}/config\n'
        "done\n"
        f"exit {status}\n"
    )
    (bin_dir / "uvx").chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "ZIZMOR_RUNNER": "uvx"}


def run_scan(tmp_path: Path, *args: str, status: int = 0, **env: str):
    return subprocess.run(
        ["bash", str(SCAN_SH), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={**fake_uvx(tmp_path, status), **env},
    )


@pytest.mark.parametrize(
    ("status", "expected", "message"),
    [
        pytest.param(0, 0, "", id="clean-scan-passes"),
        pytest.param(3, 0, "No workflow, action", id="nothing-to-audit-passes"),
        pytest.param(13, 13, "zizmor-failed-on-my-pr", id="findings-fail-and-point-to-docs"),
        pytest.param(1, 2, "zizmor did not run", id="zizmor-error-is-not-a-finding"),
        pytest.param(125, 2, "zizmor did not run", id="docker-failure-is-not-a-finding"),
    ],
)
def test_zizmor_scan_exit_status(tmp_path: Path, status: int, expected: int, message: str):
    (tmp_path / "src").mkdir()
    result = run_scan(tmp_path, "src", status=status)
    assert result.returncode == expected
    assert message in result.stderr


def test_zizmor_errors_reach_a_socket_stderr_outside_actions(tmp_path: Path):
    (tmp_path / "src").mkdir()
    env = fake_uvx(tmp_path, 0)
    (tmp_path / "bin" / "uvx").write_text("#!/bin/bash\necho 'zizmor says hi' >&2\n")
    reader, writer = socket.socketpair()
    with reader, writer:
        result = subprocess.run(
            ["bash", str(SCAN_SH), "src"], cwd=tmp_path, stderr=writer, env=env, check=False
        )
        writer.shutdown(socket.SHUT_WR)
        stderr = reader.makefile().read()
    assert result.returncode == 0, stderr
    assert "zizmor says hi" in stderr


@pytest.mark.parametrize(
    ("base_files", "online", "expected_config", "offline"),
    [
        pytest.param({}, "0", "rules: {}\n", True, id="no-config-means-empty-rules"),
        pytest.param(
            {".github/zizmor.yml": "rules: {a: {disable: true}}\n", "zizmor.yml": "other\n"},
            "0",
            "rules: {a: {disable: true}}\n",
            True,
            id="config-follows-zizmor-discovery-order",
        ),
        pytest.param({}, "1", "rules: {}\n", False, id="online-mode-drops-offline"),
    ],
)
def test_zizmor_scan_config_and_mode(
    tmp_path: Path, base_files: dict[str, str], online: str, expected_config: str, offline: bool
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "zizmor.yml").write_text("from the pull request\n")
    for name, content in base_files.items():
        (tmp_path / "base" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "base" / name).write_text(content)
    (tmp_path / "base").mkdir(exist_ok=True)
    result = run_scan(tmp_path, "src", "base", ZIZMOR_ONLINE=online)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "config").read_text() == expected_config
    args = (tmp_path / "args").read_text()
    assert ("--offline" in args) is offline
    assert args.startswith("zizmor@1.")


ACTION = "name: a\ndescription: a\nruns:\n  using: composite\n  steps: []\n"


@pytest.mark.parametrize(
    ("files", "links", "rejected"),
    [
        pytest.param({"x/action.yml": ACTION}, {}, [], id="regular-action-passes"),
        pytest.param(
            {"README.md": "hi"}, {"docs/link.md": "../README.md"}, [], id="unrelated-symlink-passes"
        ),
        pytest.param(
            {"docs/notes.txt": ACTION},
            {"x/action.yml": "../docs/notes.txt"},
            ["x/action.yml"],
            id="symlinked-action-yml-rejected",
        ),
        pytest.param(
            {"docs/notes.txt": ACTION},
            {"x/action.yaml": "../docs/notes.txt"},
            ["x/action.yaml"],
            id="symlinked-action-yaml-rejected",
        ),
        pytest.param(
            {},
            {"x/action.yml": "/home/runner/work/repo/repo/docs/notes.txt"},
            ["x/action.yml"],
            id="dangling-absolute-action-symlink-rejected",
        ),
        pytest.param(
            {"docs/wf.txt": "on: push\n"},
            {".github/workflows/ci.yml": "../../docs/wf.txt"},
            [".github/workflows/ci.yml"],
            id="symlinked-workflow-rejected",
        ),
        pytest.param(
            {"ci/workflows/ci.yml": "on: push\n"},
            {".github": "ci"},
            [".github"],
            id="symlinked-github-dir-rejected",
        ),
        pytest.param(
            {"dependabot.yml": "version: 2\n"},
            {"pkg/.github/dependabot.yml": "../../dependabot.yml"},
            ["pkg/.github/dependabot.yml"],
            id="nested-github-symlink-rejected",
        ),
        pytest.param(
            {"notes.txt": ACTION},
            {".git/x/action.yml": "../../notes.txt"},
            [],
            id="git-dir-ignored",
        ),
        pytest.param(
            {"hooks.txt": "repos: []\n"},
            {".pre-commit-config.yaml": "hooks.txt"},
            [".pre-commit-config.yaml"],
            id="symlinked-yaml-anywhere-rejected",
        ),
        pytest.param(
            {"notes.txt": ACTION},
            {"x,y/action.yml": "../notes.txt"},
            ["x%2Cy/action.yml"],
            id="annotation-path-is-escaped",
        ),
    ],
)
def test_zizmor_scan_rejects_symlinked_actions_files(
    tmp_path: Path, files: dict[str, str], links: dict[str, str], rejected: list[str]
):
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    for name, target in links.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).symlink_to(target)
    result = run_scan(tmp_path, ".")
    assert result.returncode == (1 if rejected else 0)
    assert re.findall(r"^::error file=([^,]+),", result.stderr, re.MULTILINE) == rejected


@pytest.mark.parametrize(
    ("path", "rejected"),
    [
        pytest.param("x/action.yml", False, id="plain-path-passes"),
        pytest.param("x,title=ok/action.yml", True, id="comma-rejected"),
        pytest.param("x::warning::y/action.yml", True, id="colon-rejected"),
        pytest.param("a%0A/action.yml", True, id="percent-rejected"),
        pytest.param("a\n::stop-commands::t/action.yml", True, id="newline-rejected"),
        pytest.param("x,y/notes.txt", False, id="non-yaml-ignored"),
    ],
)
def test_zizmor_scan_rejects_paths_annotations_cannot_carry(
    tmp_path: Path, path: str, rejected: bool
):
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(ACTION)
    result = run_scan(tmp_path, ".")
    assert result.returncode == (1 if rejected else 0)
    error = [line for line in result.stderr.splitlines() if line.startswith("::error")]
    assert len(error) == (1 if rejected else 0)
    assert all("\n" not in line and "::stop-commands::" not in line for line in error)


@pytest.mark.parametrize(
    ("in_actions", "edit", "rejected"),
    [
        pytest.param("true", True, True, id="working-tree-differs-in-actions"),
        pytest.param("true", False, False, id="clean-checkout-passes"),
        pytest.param("", True, False, id="local-uncommitted-edits-are-scanned"),
    ],
)
def test_zizmor_scan_requires_committed_bytes(
    tmp_path: Path, in_actions: str, edit: bool, rejected: bool
):
    repo = tmp_path / "src"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                   "commit", "-qm", "c"]):  # fmt: skip
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    if edit:
        (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\r\n")
    result = run_scan(tmp_path, "src", GITHUB_ACTIONS=in_actions)
    assert result.returncode == (1 if rejected else 0), result.stderr
    assert ("differs from its committed bytes" in result.stderr) is rejected


@pytest.mark.parametrize(
    ("write", "env"),
    [
        pytest.param("echo", {"GITHUB_ACTIONS": "true"}, id="in-actions"),
        pytest.param("printf", {"GITHUB_ACTIONS": "true"}, id="no-trailing-newline"),
        pytest.param("echo", {}, id="without-github-actions"),
    ],
)
def test_zizmor_scan_quotes_zizmor_errors(tmp_path: Path, write: str, env: dict[str, str]):
    (tmp_path / "src").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uvx").write_text(f"#!/bin/bash\n{write} '::error::injected' >&2\nexit 0\n")
    (bin_dir / "uvx").chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCAN_SH), "src"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "ZIZMOR_RUNNER": "uvx", **env},
    )
    lines = result.stderr.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("::stop-commands::"))
    token = lines[start].split("::")[2]
    assert [line for line in lines[start + 1 :] if line] == ["::error::injected", f"::{token}::"]


def test_zizmor_scan_failure_points_to_exception_docs():
    assert "ci-workflows.html#zizmor-failed-on-my-pr" in SCAN_SH.read_text()
    docs = (ROOT / "docs" / "ci-workflows.md").read_text()
    assert "### zizmor failed on my PR?\n{: #zizmor-failed-on-my-pr }\n" in docs


@pytest.mark.parametrize(
    "workflow",
    [
        pytest.param("python-ci", id="python-ci-lint-and-test-checks"),
        pytest.param("ts-ci", id="ts-ci-lint-and-test-checks"),
    ],
)
def test_shared_ci_job_names(workflow: str):
    jobs = job_blocks((WORKFLOWS_DIR / f"{workflow}.yml").read_text())
    assert list(jobs) == ["lint", "test"]
    for job, block in jobs.items():
        assert re.search(rf"^    name: {job}$", block, re.MULTILINE), job


@pytest.mark.parametrize(
    ("workflow", "jobs"),
    [
        pytest.param(
            "zizmor-scan", ["zizmor", "zizmor-pins"], id="shared-scan-owns-the-zizmor-checks"
        ),
        pytest.param("zizmor", ["zizmor-online"], id="own-online-scan-has-a-distinct-check"),
    ],
)
def test_zizmor_job_names(workflow: str, jobs: list[str]):
    blocks = job_blocks((WORKFLOWS_DIR / f"{workflow}.yml").read_text())
    assert list(blocks) == jobs
    for job in jobs:
        assert f"    name: {job}\n" in blocks[job] + "\n"


@pytest.mark.parametrize(
    ("job", "paths"),
    [
        pytest.param("zizmor", ["src", "base", "gate"], id="scan"),
        pytest.param("zizmor-pins", ["src", "base", "gate"], id="pins"),
    ],
)
def test_zizmor_scan_checkouts_do_not_overlap(job: str, paths: list[str]):
    steps = checkout_steps(job_blocks(gate_text())[job])
    assert [re.search(r"^\s+path: (\S+)$", step, re.MULTILINE)[1] for step in steps] == paths


WORKFLOW = "on: push\njobs: {}\n"
IGNORE = "on: issue_comment # zizmor: ignore[dangerous-triggers] -- gated\n"


@pytest.mark.parametrize(
    ("base", "head", "warnings"),
    [
        pytest.param(
            {"wf.yml": WORKFLOW},
            {"wf.yml": IGNORE + WORKFLOW},
            ["wf.yml:1"],
            id="added-ignore-is-flagged",
        ),
        pytest.param(
            {"wf.yml": IGNORE + WORKFLOW},
            {"wf.yml": IGNORE + WORKFLOW},
            [],
            id="unchanged-ignore-is-not-flagged",
        ),
        pytest.param(
            {"wf.yml": IGNORE + WORKFLOW},
            {"wf.yml": WORKFLOW + IGNORE},
            [],
            id="moved-ignore-is-not-flagged",
        ),
        pytest.param(
            {"wf.yml": IGNORE},
            {"wf.yml": IGNORE + IGNORE},
            ["wf.yml:2"],
            id="second-copy-of-an-ignore-is-flagged",
        ),
        pytest.param(
            {},
            {"x/action.yaml": IGNORE},
            ["x/action.yaml:1"],
            id="ignore-in-new-action-is-flagged",
        ),
        pytest.param(
            {},
            {"notes.md": IGNORE},
            [],
            id="non-yaml-file-is-skipped",
        ),
        pytest.param(
            {},
            {"a,b:c.yml": IGNORE},
            ["a%2Cb%3Ac.yml:1"],
            id="file-name-is-escaped-in-the-annotation",
        ),
    ],
)
def test_zizmor_scan_flags_new_ignores(
    tmp_path: Path, base: dict[str, str], head: dict[str, str], warnings: list[str]
):
    for root, files in (("base", base), ("src", head)):
        (tmp_path / root).mkdir()
        for name, content in files.items():
            (tmp_path / root / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / root / name).write_text(content)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-c", textwrap.dedent(gate_script("New zizmor ignore"))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={"GITHUB_STEP_SUMMARY": str(summary), "PATH": "/usr/bin:/bin"},
    )
    found = re.findall(r"^::warning file=([^,]+),line=(\d+),", result.stdout, re.MULTILINE)
    assert [f"{name}:{line}" for name, line in found] == warnings
    assert summary.exists() == bool(warnings)
