# /// script
# requires-python = ">=3.11"
# ///
"""Compare the package versions of two lock files: what a relock or a conversion to uv moved.

Reads uv.lock, poetry.lock, pdm.lock, Pipfile.lock, and requirements files of
`name==version` lines (pip-tools, Rye's requirements.lock), whatever their
file name. A git package shows its commit beside its version. The local
projects of a uv.lock (editable, virtual and directory sources) are not
compared. A package that the new uv.lock reaches only through environment
markers (colorama on `sys_platform == 'win32'`) is reported as platform-only:
a uv.lock covers every platform, an older lock may cover only the one it was
made on.

Usage:
  git show origin/main:<dir>/uv.lock > /tmp/old.lock
  python3 compare_locks.py /tmp/old.lock <dir>/uv.lock

Exit status: 0 same versions (platform-only packages aside), 1 versions moved.
"""

import json
import re
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;\\]+)")
LOCAL_SOURCES = ("editable", "virtual", "directory")


@dataclass
class Lock:
    versions: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # Package -> the markers of every dependency edge to it, when each edge has one.
    platform_only: dict[str, set[str]] = field(default_factory=dict)
    local: set[str] = field(default_factory=set)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _toml_lock(packages: list[dict]) -> Lock:
    lock = Lock()
    markers: dict[str, list[str | None]] = defaultdict(list)
    for package in packages:
        name = normalize(package["name"])
        source = package.get("source") or {}
        if any(key in source for key in LOCAL_SOURCES) or source.get("type") == "directory":
            lock.local.add(name)
            continue
        version = str(package.get("version", "?"))
        # uv: `git = "<url>?rev=<ref>#<commit>"`; Poetry: `resolved_reference`.
        commit = str(source.get("git", "")).partition("#")[2] or source.get("resolved_reference")
        lock.versions[name].add(f"{version} (git {commit[:8]})" if commit else version)
        # uv lists dependency edges as tables; Poetry's `dependencies` is a name -> spec table.
        edges = list(package.get("dependencies") or [])
        for group in ("optional-dependencies", "dev-dependencies"):
            for deps in (package.get(group) or {}).values():
                edges += deps
        for edge in edges:
            if isinstance(edge, dict):
                markers[normalize(edge["name"])].append(edge.get("marker"))
    lock.platform_only = {name: set(found) for name, found in markers.items() if all(found)}
    return lock


def _pipfile_lock(data: dict) -> Lock:
    lock = Lock()
    for section in ("default", "develop"):
        for name, spec in (data.get(section) or {}).items():
            version = str(spec.get("version", "")).lstrip("=")
            if version:
                lock.versions[normalize(name)].add(version)
    return lock


def _requirements(text: str) -> Lock:
    lock = Lock()
    for line in text.splitlines():
        match = REQUIREMENT.match(line.strip())
        if match:
            lock.versions[normalize(match[1])].add(match[2])
    return lock


def parse(text: str) -> Lock:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        data = {}
    if "package" in data:
        return _toml_lock(data["package"])
    try:
        return _pipfile_lock(json.loads(text))
    except ValueError:
        return _requirements(text)


def compare(old: Lock, new: Lock) -> tuple[list[str], bool]:
    """Lines for each added, removed or changed package, and whether any version moved."""

    def versions(lock: Lock, name: str) -> str:
        return ", ".join(sorted(lock.versions[name]))

    def line(change: str, name: str, lock: Lock) -> str:
        text = f"{change:8} {name} {versions(lock, name)}"
        if name in lock.platform_only:
            text += f" (platform-only: {' or '.join(sorted(lock.platform_only[name]))})"
        return text

    old_names, new_names = set(old.versions), set(new.versions)
    added = [line("added", n, new) for n in sorted(new_names - old_names)]
    removed = [line("removed", n, old) for n in sorted(old_names - new_names)]
    changed = [
        f"{'changed':8} {n} {versions(old, n)} -> {versions(new, n)}"
        for n in sorted(old_names & new_names)
        if old.versions[n] != new.versions[n]
    ]
    moved = (
        bool(changed)
        or any(n not in new.platform_only for n in new_names - old_names)
        or any(n not in old.platform_only for n in old_names - new_names)
    )
    return added + removed + changed, moved


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    old, new = (parse(Path(path).read_text(encoding="utf-8")) for path in argv)
    lines, moved = compare(old, new)
    print(f"old: {len(old.versions)} packages, new: {len(new.versions)} packages")
    if old.local | new.local:
        print(f"local projects, not compared: {', '.join(sorted(old.local | new.local))}")
    for text in lines:
        print(f"  {text}")
    if moved:
        print("Versions moved.")
    elif lines:
        print("Same versions; the new lock adds or drops only platform-only packages.")
    else:
        print("Same package set and versions.")
    return 1 if moved else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
