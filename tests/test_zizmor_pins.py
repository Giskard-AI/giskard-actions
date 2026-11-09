import importlib.util
import subprocess
from pathlib import Path

import pytest

PINS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "zizmor" / "pins.py"
spec = importlib.util.spec_from_file_location("pins", PINS_PATH)
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

WORKFLOW = """on: pull_request
jobs:
  a:
    steps:
      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v4
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit_twice(tmp_path: Path, before: dict[str, str], after: dict[str, str]) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    for files in (before, after):
        for name, content in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(content)
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "--allow-empty", "-m", "c")
    return tmp_path


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        pytest.param(
            {"wf.yml": WORKFLOW},
            {"wf.yml": WORKFLOW.replace("aaaa", "bbbb", 1)},
            {("wf.yml", 5)},
            id="changed-pin-is-checked",
        ),
        pytest.param(
            {},
            {".github/workflows/ci.yml": WORKFLOW},
            {(".github/workflows/ci.yml", 5)},
            id="new-workflow-is-checked",
        ),
        pytest.param(
            {"wf.yml": WORKFLOW},
            {"wf.yml": "# comment\n" + WORKFLOW},
            set(),
            id="moved-uses-line-is-not-checked",
        ),
        pytest.param(
            {},
            {"a/action.yml": "runs:\n  steps:\n    - uses: ./local\n"},
            set(),
            id="local-action-is-not-checked",
        ),
        pytest.param({}, {"notes.md": "uses: x/y@main\n"}, set(), id="non-yaml-is-skipped"),
    ],
)
def test_added_uses(tmp_path: Path, before, after, expected):
    assert pins.added_uses(commit_twice(tmp_path, before, after)) == expected


def finding(ident: str, path: str, row: int, *, ignored: bool = False) -> dict:
    return {
        "ident": ident,
        "desc": "something",
        "url": f"https://docs.zizmor.sh/audits/#{ident}",
        "ignored": ignored,
        "locations": [
            {
                "symbolic": {"key": {"Local": {"verbatim_path": f"./{path}"}}, "kind": "Primary"},
                "concrete": {"location": {"start_point": {"row": row, "column": 0}}},
            }
        ],
    }


ADDED = {("wf.yml", 5)}


@pytest.mark.parametrize(
    ("findings", "expected", "errors"),
    [
        pytest.param(
            [finding("impostor-commit", "wf.yml", 4)],
            ["::error file=wf.yml,line=5,title=zizmor impostor-commit::"],
            1,
            id="impostor-on-an-added-line-fails",
        ),
        pytest.param(
            [finding("impostor-commit", "other.yml", 4)],
            ["::warning file=other.yml,line=5,title=zizmor impostor-commit::"],
            0,
            id="impostor-elsewhere-only-warns",
        ),
        pytest.param(
            [finding("known-vulnerable-actions", "wf.yml", 4)],
            ["::warning file=wf.yml,line=5,title=zizmor known-vulnerable-actions::"],
            0,
            id="other-online-finding-warns",
        ),
        pytest.param(
            [finding("unpinned-uses", "wf.yml", 4)], [], 0, id="offline-findings-left-to-the-gate"
        ),
        pytest.param(
            [finding("impostor-commit", "wf.yml", 4, ignored=True)],
            [],
            0,
            id="ignored-finding-is-skipped",
        ),
    ],
)
def test_annotations(findings, expected, errors):
    lines, found = pins.annotations(findings, ADDED)
    assert [line.split("::", 2)[0] + "::" + line.split("::", 2)[1] + "::" for line in lines] == (
        expected
    )
    assert found == errors


def test_no_added_reference_skips_the_scan(tmp_path: Path, capsys):
    repo = commit_twice(tmp_path, {"wf.yml": WORKFLOW}, {"README.md": "hi\n"})
    assert pins.main(repo) == 0
    assert "nothing to check online" in capsys.readouterr().out
