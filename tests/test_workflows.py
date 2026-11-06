"""Static security checks on .github/workflows/*.yml, parsed line by line (no YAML dependency)."""

import json
import re
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


ZIZMOR_WORKFLOW_FILES = [WORKFLOWS_DIR / f"{name}.yml" for name in ("zizmor", "zizmor-scan")]
ZIZMOR_IMAGE_RE = re.compile(
    r"^      ZIZMOR_IMAGE: ghcr\.io/zizmorcore/zizmor:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$",
    re.MULTILINE,
)


@pytest.mark.parametrize("path", ZIZMOR_WORKFLOW_FILES, ids=lambda p: p.stem)
def test_zizmor_image_is_digest_pinned(path: Path):
    text = path.read_text()
    assert len(ZIZMOR_IMAGE_RE.findall(text)) == 1
    assert "zizmorcore/zizmor-action" not in text


def test_zizmor_workflows_share_one_image():
    images = {ZIZMOR_IMAGE_RE.search(path.read_text())[0] for path in ZIZMOR_WORKFLOW_FILES}
    assert len(images) == 1


@pytest.mark.parametrize("path", ZIZMOR_WORKFLOW_FILES, ids=lambda p: p.stem)
def test_renovate_tracks_zizmor_image(path: Path):
    config = json.loads((WORKFLOWS_DIR.parents[1] / "renovate.json").read_text())
    (manager,) = [m for m in config["customManagers"] if m.get("datasourceTemplate") == "docker"]
    assert any(
        re.search(p.strip("/"), f".github/workflows/{path.name}")
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
        pytest.param("--persona=pedantic", id="runs-the-pedantic-persona"),
        pytest.param("--offline", id="runs-offline"),
        pytest.param("--format=github", id="annotates-findings"),
        pytest.param("--network none", id="container-has-no-network"),
        pytest.param(
            '--volume "${GITHUB_WORKSPACE}/src:/src:ro"', id="pull-request-mounted-read-only"
        ),
        pytest.param("--config=/config/zizmor.yml", id="config-from-outside-the-checkout"),
    ],
)
def test_zizmor_scan_flags(flag: str):
    assert flag in zizmor_scan_script("docker run")


def test_zizmor_scan_honours_inline_ignores():
    assert "--no-ignores" not in zizmor_scan_script("docker run")


def zizmor_scan_script(marker: str) -> str:
    (script,) = [
        s for s in run_scripts((WORKFLOWS_DIR / "zizmor-scan.yml").read_text()) if marker in s
    ]
    return script


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
    result = subprocess.run(
        ["bash", "-c", zizmor_scan_script("find . ")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if rejected else 0)
    assert re.findall(r"^::error file=([^,]+),", result.stdout, re.MULTILINE) == rejected


def test_zizmor_scan_failure_points_to_exception_docs():
    script = zizmor_scan_script("docker run")
    assert "ci-workflows.html#zizmor-failed-on-my-pr" in script
    docs = (WORKFLOWS_DIR.parents[1] / "docs" / "ci-workflows.md").read_text()
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
    ("workflow", "job"),
    [
        pytest.param("zizmor-scan", "zizmor", id="shared-scan-owns-the-zizmor-check"),
        pytest.param("zizmor", "zizmor-pedantic", id="own-pedantic-scan-has-a-distinct-check"),
    ],
)
def test_zizmor_job_names(workflow: str, job: str):
    jobs = job_blocks((WORKFLOWS_DIR / f"{workflow}.yml").read_text())
    assert list(jobs) == [job]
    assert f"    name: {job}\n" in jobs[job] + "\n"


def test_zizmor_scan_checkouts_do_not_overlap():
    steps = checkout_steps((WORKFLOWS_DIR / "zizmor-scan.yml").read_text())
    paths = [re.search(r"^\s+path: (\S+)$", step, re.MULTILINE)[1] for step in steps]
    assert paths == ["src", "base"]


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
        ["bash", "-c", textwrap.dedent(zizmor_scan_script("New zizmor ignore"))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={"GITHUB_STEP_SUMMARY": str(summary), "PATH": "/usr/bin:/bin"},
    )
    found = re.findall(r"^::warning file=([^,]+),line=(\d+),", result.stdout, re.MULTILINE)
    assert [f"{name}:{line}" for name, line in found] == warnings
    assert summary.exists() == bool(warnings)
