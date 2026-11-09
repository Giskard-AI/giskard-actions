import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

PINS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zizmor" / "pins.py"
spec = importlib.util.spec_from_file_location("pins", PINS_PATH)
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

WF = ".github/workflows/ci.yml"
WORKFLOW = """on: pull_request
jobs:
  a:
    steps:
      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v4
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def write(repo: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)


def commits(repo: Path, *states: dict[str, str]) -> Path:
    repo.mkdir(exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    for files in states:
        write(repo, files)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    return repo


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        pytest.param(
            {WF: WORKFLOW},
            {WF: WORKFLOW.replace("aaaa", "bbbb", 1)},
            {WF: {5}},
            id="changed-pin-is-checked",
        ),
        pytest.param({}, {WF: WORKFLOW}, {WF: {1, 2, 3, 4, 5}}, id="new-workflow-is-checked"),
        pytest.param(
            {WF: WORKFLOW}, {WF: "# comment\n" + WORKFLOW}, {WF: {1}}, id="only-added-lines"
        ),
        pytest.param(
            {WF: WORKFLOW},
            {WF: WORKFLOW + "      - uses:\n          other/action@cccc\n"},
            {WF: {6, 7}},
            id="reference-on-the-next-line",
        ),
        pytest.param(
            {}, {"x/action.yaml": "runs: {steps: [{uses: a/b@c}]}\n"},
            {"x/action.yaml": {1}}, id="flow-mapping-action",
        ),
        pytest.param(
            {}, {".pre-commit-config.yaml": "repos: []\n"},
            {".pre-commit-config.yaml": {1}}, id="pre-commit-config",
        ),
        pytest.param(
            {}, {".github/my wf/action.yml": "a: 1\n", ".github/workflows/é.yml": "a: 1\n"},
            {".github/my wf/action.yml": {1}, ".github/workflows/é.yml": {1}},
            id="paths-with-space-and-non-ascii",
        ),
        pytest.param(
            {}, {"config/app.yml": "a: 1\n", ".github/workflows/sub/x.yml": "a: 1\n"},
            {}, id="files-zizmor-does-not-collect",
        ),
        pytest.param(
            {WF: WORKFLOW},
            {".gitattributes": "*.yml -diff\n", WF: WORKFLOW.replace("aaaa", "bbbb", 1)},
            {WF: {5}},
            id="gitattributes-cannot-hide-the-diff",
        ),
        pytest.param({WF: WORKFLOW}, {WF: ""}, {}, id="removed-lines-only"),
    ],
)  # fmt: skip
def test_added_lines(tmp_path: Path, before, after, expected):
    assert pins.added_lines(commits(tmp_path, before, after)) == expected


def test_first_commit_is_compared_with_nothing(tmp_path: Path):
    assert pins.added_lines(commits(tmp_path, {WF: "a: 1\n"})) == {WF: {1}}


def test_merge_commit_is_compared_with_its_first_parent(tmp_path: Path):
    repo = commits(tmp_path, {WF: WORKFLOW})
    git(repo, "checkout", "-q", "-b", "pr")
    write(repo, {WF: WORKFLOW + "      - uses: pr/added@dddd\n"})
    git(repo, "commit", "-qam", "pr")
    git(repo, "checkout", "-q", "main")
    write(repo, {"README.md": "base moved\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    assert pins.added_lines(repo) == {WF: {6}}


def location(path: str, start: int, end: int, kind: str, end_column: int = 5) -> dict:
    return {
        "symbolic": {"key": {"Local": {"verbatim_path": f"./{path}"}}, "kind": kind},
        "concrete": {
            "location": {
                "start_point": {"row": start - 1, "column": 0},
                "end_point": {"row": end - 1, "column": end_column},
            }
        },
    }


def finding(ident: str, line: int, *, step: tuple[int, int] | None = None, **extra) -> dict:
    locations = [location(WF, line, line, "Primary")]
    if step:
        locations.insert(0, location(WF, *step, "Related", end_column=0))
    return {
        "ident": ident,
        "desc": "something",
        "url": f"https://docs.zizmor.sh/audits/#{ident}",
        "locations": locations,
        **extra,
    }


ADDED = {WF: {7}}


@pytest.mark.parametrize(
    ("findings", "expected", "errors"),
    [
        pytest.param([finding("impostor-commit", 7)], ["error 7"], 1, id="impostor-on-added-line"),
        pytest.param(
            [finding("impostor-commit", 5, step=(5, 8))], ["error 5"], 1,
            id="impostor-in-a-changed-step",
        ),
        pytest.param(
            [finding("impostor-commit", 5, step=(5, 7))], ["warning 5"], 0,
            id="step-span-ends-before-the-added-line",
        ),
        pytest.param([finding("impostor-commit", 2)], ["warning 2"], 0, id="impostor-elsewhere"),
        pytest.param(
            [finding("impostor-commit", 7, ignored=True)], ["error 7"], 1,
            id="ignored-impostor-still-fails",
        ),
        pytest.param(
            [finding("known-vulnerable-actions", 7)], ["warning 7"], 0, id="other-online-warns"
        ),
        pytest.param(
            [finding("known-vulnerable-actions", 7, ignored=True)], [], 0,
            id="ignored-other-online-is-skipped",
        ),
        pytest.param([finding("unpinned-uses", 7)], [], 0, id="offline-findings-left-to-gate"),
    ],
)  # fmt: skip
def test_annotations(findings, expected, errors):
    lines, found = pins.annotations(findings, ADDED)
    assert [
        f"{line[2:].split(' ', 1)[0]} {line.split('line=')[1].split(',')[0]}" for line in lines
    ] == expected
    assert found == errors


def test_unreadable_same_owner_references_are_skipped(tmp_path: Path, monkeypatch):
    repo = commits(
        tmp_path,
        {
            WF: "jobs:\n  a:\n    uses: Giskard-AI/private/.github/workflows/x.yml@abc\n"
            "  b:\n    steps:\n      - uses: Giskard-AI/public@def # v1\n"
            "      - uses: other/private@123\n"
        },
    )
    monkeypatch.setattr(pins, "readable", lambda owner, repo: repo == "public")
    dest = tmp_path / "staged"
    skipped = pins.stage(repo, [WF], dest, "Giskard-AI")
    assert skipped == [(WF, 3, "Giskard-AI/private/.github/workflows/x.yml@abc")]
    staged = (dest / WF).read_text().splitlines()
    assert staged[2] == f"    uses: {pins.SKIPPED_REF}"
    assert staged[5:] == [
        "      - uses: Giskard-AI/public@def # v1",
        "      - uses: other/private@123",
    ]


def test_staged_files_are_the_committed_bytes(tmp_path: Path, monkeypatch):
    repo = commits(tmp_path, {WF: WORKFLOW})
    (repo / WF).write_text("changed in the working tree only\n")
    pins.stage(repo, [WF], tmp_path / "staged", "Giskard-AI")
    assert (tmp_path / "staged" / WF).read_text() == WORKFLOW


def fake_zizmor(tmp_path: Path, monkeypatch, stdout: str, status: int) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "out.json").write_text(stdout)
    (bin_dir / "uvx").write_text(
        "#!/bin/bash\necho 'zizmor says ::error::fake' >&2\n"
        f"cat {tmp_path}/out.json\nexit {status}\n"
    )
    (bin_dir / "uvx").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("ZIZMOR_RUNNER", "uvx")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "Giskard-AI")


@pytest.mark.parametrize(
    ("findings", "status", "expected", "message"),
    [
        pytest.param([finding("impostor-commit", 6)], 14, 1, "::error file=", id="impostor-fails"),
        pytest.param([], 0, 0, "", id="clean-passes"),
        pytest.param([], 1, 1, "stopped with status 1", id="zizmor-error-fails"),
    ],
)
def test_main(tmp_path: Path, monkeypatch, capsys, findings, status, expected, message):
    repo = commits(
        tmp_path / "repo", {WF: WORKFLOW}, {WF: WORKFLOW + "      - uses: x/y@eeee # v1\n"}
    )
    fake_zizmor(tmp_path, monkeypatch, json.dumps(findings), status)
    assert pins.main(repo, repo) == expected
    out, err = capsys.readouterr()
    assert message in out
    assert "::stop-commands::" in err and "zizmor says ::error::fake" in err


def test_no_added_line_skips_the_scan(tmp_path: Path, capsys):
    repo = commits(tmp_path, {WF: WORKFLOW}, {"README.md": "hi\n"})
    assert pins.main(repo, repo) == 0
    assert "nothing to check online" in capsys.readouterr().out
