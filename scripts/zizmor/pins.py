"""Online check of the action references a pull request adds or changes.

Usage: pins.py SRC

SRC is a checkout of the pull request with its parent commit (fetch-depth: 2).
When the last commit adds or changes no `uses:` line in a YAML file, nothing
runs. Otherwise scan.sh runs zizmor online. The job fails on an
`impostor-commit` finding on an added line, or when zizmor could not check a
reference; the other online findings become warnings, since they can appear
on unchanged lines as soon as an advisory or a new tag is published.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCAN = Path(__file__).with_name("scan.sh")
ONLINE_AUDITS = {
    "impostor-commit",
    "known-vulnerable-actions",
    "ref-confusion",
    "ref-version-mismatch",
    "stale-action-refs",
}
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,\d+)? @@")
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*['\"]?(?!\./)\S")


def added_uses(src: Path) -> set[tuple[str, int]]:
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(src),
            "diff",
            "-U0",
            "--no-color",
            "HEAD^1",
            "HEAD",
            "--",
            "*.yml",
            "*.yaml",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    added, path, line = set(), None, 0
    for text in diff.splitlines():
        if text.startswith("+++ "):
            path = text[6:] if text.startswith("+++ b/") else None
        elif match := HUNK.match(text):
            line = int(match[1])
        elif text.startswith("+") and path:
            if USES.match(text[1:]):
                added.add((path, line))
            line += 1
    return added


def primary_location(finding: dict) -> tuple[str, int]:
    location = next(
        (loc for loc in finding["locations"] if loc["symbolic"]["kind"] == "Primary"),
        finding["locations"][0],
    )
    path = location["symbolic"]["key"]["Local"]["verbatim_path"].removeprefix("./")
    return path, location["concrete"]["location"]["start_point"]["row"] + 1


def escape(value: str, *, prop: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if prop else value


def annotations(findings: list[dict], added: set[tuple[str, int]]) -> tuple[list[str], int]:
    lines, errors = [], 0
    for finding in findings:
        if finding["ident"] not in ONLINE_AUDITS or finding.get("ignored"):
            continue
        path, line = primary_location(finding)
        level = "warning"
        if finding["ident"] == "impostor-commit" and (path, line) in added:
            level, errors = "error", errors + 1
        lines.append(
            f"::{level} file={escape(path, prop=True)},line={line},"
            f"title=zizmor {finding['ident']}::{escape(finding['desc'])}. {finding['url']}"
        )
    return lines, errors


def main(src: Path) -> int:
    added = added_uses(src)
    if not added:
        print("No action reference added or changed: nothing to check online.")
        return 0
    result = subprocess.run(
        ["bash", str(SCAN), str(src)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ZIZMOR_ONLINE": "1", "ZIZMOR_FORMAT": "json"},
    )
    sys.stderr.write(result.stderr)
    if result.returncode not in (0, 10, 11, 12, 13, 14):
        print(
            f"::error title=zizmor-pins::zizmor stopped with status {result.returncode}: it could "
            "not check the new action references (often a GitHub API error). Re-run the job."
        )
        return 1
    lines, errors = annotations(json.loads(result.stdout or "[]"), added)
    for line in lines:
        print(line)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
