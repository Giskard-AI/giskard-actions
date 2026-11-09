"""Online check of the action references a pull request adds or changes.

Usage: pins.py SRC BASE

SRC is a checkout of the pull request with its parent commit (fetch-depth: 2);
HEAD^1 is the base on pull_request (merge commit) and the previous entry on
merge_group. BASE holds the base commit's zizmor config.

When the diff adds no line to a workflow, action or pre-commit file, nothing
runs. Otherwise scan.sh runs zizmor online on the committed bytes of those
files only. The job fails on an `impostor-commit` finding that touches an added
line (ignored or not: an impostor commit is never a valid exception), or when
zizmor cannot finish. The other online findings become warnings, since they can
appear on unchanged lines as soon as an advisory or a new tag is published.

The job token reads only its own repository, and zizmor stops on a reference
it cannot read. References to other repositories of the same owner that the
token cannot read are replaced by a local path before the scan, with a warning.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SCAN = Path(__file__).with_name("scan.sh")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
ONLINE_AUDITS = {
    "impostor-commit",
    "known-vulnerable-actions",
    "ref-confusion",
    "ref-version-mismatch",
    "stale-action-refs",
}
COLLECTED = re.compile(r"^(?:\.github/workflows/[^/]+|(?:.*/)?action|\.pre-commit-config)\.ya?ml$")
HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
REMOTE_REF = re.compile(
    r"(?<![\w./-])(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+)(?:/[^@\s'\"]*)?@[^\s'\"#}\],]+"
)
SKIPPED_REF = "./.github/zizmor-pins-skipped"


class PinsError(RuntimeError):
    pass


def git(src: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    return subprocess.run(
        ["git", "-C", str(src), "-c", "core.quotePath=false", *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout


def added_lines(src: Path) -> dict[str, set[int]]:
    """Added line numbers, by file zizmor collects, between HEAD^1 (or nothing) and HEAD."""
    parent = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "--verify", "-q", "HEAD^1"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    diff = git(
        src,
        "diff", "--text", "--no-ext-diff", "--no-textconv", "--no-color",
        "--src-prefix=a/", "--dst-prefix=b/", "-U0",
        parent or EMPTY_TREE, "HEAD", "--", "*.yml", "*.yaml",
    )  # fmt: skip
    added: dict[str, set[int]] = {}
    path, row, old_left, new_left = None, 0, 0, 0
    for text in diff.split("\n"):
        if old_left or new_left:
            if text.startswith("-"):
                old_left -= 1
            elif text.startswith("+"):
                if path:
                    added.setdefault(path, set()).add(row)
                row, new_left = row + 1, new_left - 1
            continue
        if text.startswith("+++ "):
            name = text[4:].removesuffix("\t")
            if name == "/dev/null":
                path = None
            elif name.startswith("b/"):
                path = name[2:] if COLLECTED.match(name[2:]) else None
            else:
                raise PinsError(f"Cannot read the diff header {name!r}.")
        elif match := HUNK.match(text):
            old_left = int(match[1]) if match[1] is not None else 1
            row = int(match[2])
            new_left = int(match[3]) if match[3] is not None else 1
    return added


def readable(owner: str, repo: str) -> bool:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise PinsError(f"GitHub API error {error.code} on {owner}/{repo}.") from error


def stage(src: Path, paths: list[str], dest: Path, owner: str) -> list[tuple[str, int, str]]:
    """Writes the committed bytes of PATHS under DEST, minus unreadable same-owner refs."""
    skipped, cache = [], {}
    for path in paths:
        text = git(src, "cat-file", "blob", f"HEAD:{path}")
        lines = text.split("\n")
        for number, line in enumerate(lines):
            if "uses" not in line:
                continue
            for match in REMOTE_REF.finditer(line):
                if match["owner"].lower() != owner.lower():
                    continue
                key = (match["owner"], match["repo"])
                if key not in cache:
                    cache[key] = readable(*key)
                if not cache[key]:
                    skipped.append((path, number + 1, match[0]))
                    lines[number] = line.replace(match[0], SKIPPED_REF)
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines))
    return skipped


def rows(location: dict) -> range:
    span = location["concrete"]["location"]
    start, end = span["start_point"]["row"] + 1, span["end_point"]["row"] + 1
    if span["end_point"]["column"] == 0 and end > start:
        end -= 1
    return range(start, end + 1)


def escape(value: str, *, prop: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if prop else value


def annotations(findings: list[dict], added: dict[str, set[int]]) -> tuple[list[str], int]:
    lines, errors = [], 0
    for finding in findings:
        ident = finding["ident"]
        impostor = ident == "impostor-commit"
        if ident not in ONLINE_AUDITS or (finding.get("ignored") and not impostor):
            continue
        located = [
            (loc["symbolic"]["key"]["Local"]["verbatim_path"].removeprefix("./"), rows(loc))
            for loc in finding["locations"]
        ]
        touched = any(added.get(path, set()).intersection(span) for path, span in located)
        primary = next(
            (
                place
                for place, loc in zip(located, finding["locations"], strict=True)
                if loc["symbolic"]["kind"] == "Primary"
            ),
            located[0],
        )
        level = "warning"
        if impostor and touched:
            level, errors = "error", errors + 1
        lines.append(
            f"::{level} file={escape(primary[0], prop=True)},line={primary[1].start},"
            f"title=zizmor {ident}::{escape(finding['desc'])}. {finding['url']}"
        )
    return lines, errors


def print_quoted(text: str) -> None:
    """Prints TEXT with workflow commands off: zizmor quotes values from the scanned files."""
    if text:
        token = uuid.uuid4().hex
        print(f"::stop-commands::{token}\n{text.rstrip()}\n::{token}::", file=sys.stderr)


def main(src: Path, base: Path) -> int:
    added = added_lines(src)
    if not added:
        print("No workflow, action or pre-commit line added: nothing to check online.")
        return 0
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    with tempfile.TemporaryDirectory() as tmp:
        for path, line, ref in stage(src, sorted(added), Path(tmp), owner):
            print(
                f"::warning file={escape(path, prop=True)},line={line},title=zizmor-pins::"
                f"{escape(ref)} is not readable with this job's token, so it is not checked online."
            )
        result = subprocess.run(
            ["bash", str(SCAN), tmp, str(base)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "ZIZMOR_ONLINE": "1", "ZIZMOR_FORMAT": "json-v1"},
        )
    print_quoted(result.stderr)
    if result.returncode == 125:
        print("::error title=zizmor-pins::Could not start the zizmor container. Re-run the job.")
        return 1
    if result.returncode not in (0, 10, 11, 12, 13, 14):
        print(
            f"::error title=zizmor-pins::zizmor stopped with status {result.returncode} "
            "before checking the new action references; its message is in the log above."
        )
        return 1
    try:
        findings = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as error:
        raise PinsError(f"zizmor's JSON output cannot be read: {error}") from error
    lines, errors = annotations(findings, added)
    for line in lines:
        print(line)
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
    except (PinsError, subprocess.CalledProcessError) as error:
        print(f"::error title=zizmor-pins::{escape(str(error))}")
        sys.exit(1)
