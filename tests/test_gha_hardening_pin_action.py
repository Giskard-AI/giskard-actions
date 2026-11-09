import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "skills" / "gha-hardening" / "scripts" / "pin-action.sh"
)
V4 = "4" * 40
V4_1 = "1" * 40
V2 = "2" * 40
V1_2_0 = "a" * 40
V1_2_1_TAG = "b" * 40
V1_2_1 = "c" * 40
MAIN = "d" * 40
# `git ls-remote --tags`: v4 and v4.2.2 share a commit; v1.2.1 is annotated.
TAGS = f"""\
{V4}\trefs/tags/v4
{V4_1}\trefs/tags/v4.1.0
{V4}\trefs/tags/v4.2.2
{V2}\trefs/tags/v2.0.0
{V1_2_0}\trefs/tags/v1.2.0
{V1_2_1_TAG}\trefs/tags/v1.2.1
{V1_2_1}\trefs/tags/v1.2.1^{{}}
"""
HEADS = {"release/v1": V1_2_1, "main": MAIN}


@pytest.fixture
def pin(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "tags").write_text(TAGS)
    heads = "\n".join(
        f'  "refs/heads/{name}") echo "{sha}\trefs/heads/{name}" ;;' for name, sha in HEADS.items()
    )
    (bin_dir / "git").write_text(
        "#!/bin/bash\n"
        f"[[ $2 == --tags ]] && exec cat {tmp_path}/tags\n"
        'case "$3" in\n'
        f"{heads}\n"
        "esac\n"
    )
    (bin_dir / "git").chmod(0o755)

    def run(ref: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), f"actions/checkout@{ref}"],
            capture_output=True,
            text=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
            check=False,
        )

    return run


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        pytest.param("v4", f"{V4} # v4.2.2", id="major-tag-takes-its-highest-release"),
        pytest.param("v4.1", f"{V4_1} # v4.1.0", id="minor-tag-takes-its-highest-patch"),
        pytest.param("v1.2", f"{V1_2_1} # v1.2.1", id="minor-tag-is-not-read-as-major-2"),
        pytest.param("1.2", f"{V1_2_1} # v1.2.1", id="minor-without-v"),
        pytest.param("v4.2.2", f"{V4} # v4.2.2", id="full-version-stays-that-version"),
        pytest.param("v1.2.1", f"{V1_2_1} # v1.2.1", id="annotated-tag-resolves-to-its-commit"),
        pytest.param(V4, f"{V4} # v4.2.2", id="sha-gets-its-most-specific-tag"),
        pytest.param("release/v1", f"{V1_2_1} # v1.2.1", id="branch-gets-the-tag-of-its-head"),
        pytest.param("main", f"{MAIN} # main (no release tag", id="untagged-branch-asks-a-human"),
    ],
)
def test_ref_resolution(pin, ref, expected):
    result = pin(ref)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"actions/checkout@{expected}")


@pytest.mark.parametrize("ref", ["v9", "v4.9", "no-such-branch"])
def test_unknown_ref_fails(pin, ref):
    result = pin(ref)
    assert result.returncode == 1
    assert f"Cannot resolve actions/checkout@{ref}" in result.stderr
