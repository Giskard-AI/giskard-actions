"""Deterministic dependency bump detection from manifest files."""

import json
import logging
import re
import tomllib
from pathlib import Path
from xml.etree import ElementTree as ET

from .git_ops import git_show
from .models import CommitDigest, ConsolidatedCommit, DependencyUpdate, TagRange

LOG = logging.getLogger("release_notes.deps")

_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)(\[[^\]]+\])?(.*)$")


def _parse_python_req(req: str) -> tuple[str, str]:
    req = (req or "").strip()
    m = _REQ_NAME_RE.match(req)
    if not m:
        return req, req
    base = m.group(1)
    extras = m.group(2) or ""
    rest = (m.group(3) or "").strip()
    name = f"{base}{extras}".strip()
    if "==" in req:
        pinned = req.split("==", 1)[1].strip().split(";", 1)[0].strip()
        return name, pinned
    return name, rest or req


def _parse_pyproject_deps(content: str) -> dict[str, str]:
    data = tomllib.loads(content)
    out: dict[str, str] = {}

    deps = (data.get("project") or {}).get("dependencies") or []
    for req in deps:
        name, ver = _parse_python_req(str(req))
        out[name] = ver

    dep_groups = data.get("dependency-groups") or {}
    if isinstance(dep_groups, dict):
        for group, reqs in dep_groups.items():
            if not isinstance(reqs, list):
                continue
            for req in reqs:
                name, ver = _parse_python_req(str(req))
                key = f"{name} ({group})" if name in out else name
                out[key] = ver

    return out


def _parse_package_json_deps(content: str) -> dict[str, str]:
    data = json.loads(content)
    out: dict[str, str] = {}
    deps = data.get("dependencies") or {}
    dev = data.get("devDependencies") or {}
    if isinstance(deps, dict):
        for k, v in deps.items():
            out[str(k)] = str(v)
    if isinstance(dev, dict):
        for k, v in dev.items():
            key = f"{k} (dev)" if k in out else str(k)
            out[key] = str(v)
    return out


def _parse_pom_versions(content: str) -> dict[str, str]:
    root = ET.fromstring(content)
    ns = ""
    if root.tag.startswith("{") and "}" in root.tag:
        ns = root.tag.split("}", 1)[0] + "}"

    def q(tag: str) -> str:
        return f"{ns}{tag}"

    out: dict[str, str] = {}
    props = root.find(q("properties"))
    if props is not None:
        for child in list(props):
            tag = child.tag
            if ns and tag.startswith(ns):
                tag = tag[len(ns):]
            val = (child.text or "").strip()
            if tag and val:
                out[tag] = val

    deps_el = root.find(q("dependencies"))
    if deps_el is not None:
        for dep in deps_el.findall(q("dependency")):
            gid = (dep.findtext(q("groupId")) or "").strip()
            aid = (dep.findtext(q("artifactId")) or "").strip()
            ver = (dep.findtext(q("version")) or "").strip()
            if gid and aid and ver and not ver.startswith("${"):
                out[f"{gid}:{aid}"] = ver

    return out


def _dependency_bumps_for_file(
    *, before: str | None, after: str | None, manifest_path: str
) -> list[DependencyUpdate]:
    if before is None or after is None:
        return []

    if manifest_path.endswith("pyproject.toml"):
        before_map = _parse_pyproject_deps(before)
        after_map = _parse_pyproject_deps(after)
    elif manifest_path.endswith("package.json"):
        before_map = _parse_package_json_deps(before)
        after_map = _parse_package_json_deps(after)
    elif manifest_path.endswith("pom.xml"):
        before_map = _parse_pom_versions(before)
        after_map = _parse_pom_versions(after)
    else:
        return []

    bumps: list[DependencyUpdate] = []
    for name in sorted(set(before_map) | set(after_map)):
        bv = before_map.get(name)
        av = after_map.get(name)
        if bv is None or av is None or bv == av:
            continue
        bumps.append(
            DependencyUpdate(
                manifest_path=manifest_path, name=name,
                from_version=bv, to_version=av,
            )
        )
    return bumps


def dependency_updates_for_commit(
    commit_digest: CommitDigest, *, repo_root: Path, manifest_paths: tuple[str, ...]
) -> list[DependencyUpdate]:
    manifest_set = set(manifest_paths)
    changed_manifests = [p for p in commit_digest.changed_files if p in manifest_set]
    if not changed_manifests:
        return []

    bumps: list[DependencyUpdate] = []
    for mp in changed_manifests:
        before = git_show(f"{commit_digest.sha}^", mp, repo_root=repo_root)
        after = git_show(commit_digest.sha, mp, repo_root=repo_root)
        bumps.extend(_dependency_bumps_for_file(before=before, after=after, manifest_path=mp))
    return bumps


def dependency_updates_for_range(
    tag_range: TagRange, *, repo_root: Path, manifest_paths: tuple[str, ...]
) -> list[DependencyUpdate]:
    bumps: list[DependencyUpdate] = []
    for mp in manifest_paths:
        before = git_show(tag_range.previous_tag, mp, repo_root=repo_root)
        after = git_show(tag_range.current_tag, mp, repo_root=repo_root)
        bumps.extend(_dependency_bumps_for_file(before=before, after=after, manifest_path=mp))
    return bumps


def merge_dependency_notes(
    *, range_bumps: list[DependencyUpdate], consolidations: list[ConsolidatedCommit]
) -> list[DependencyUpdate]:
    notes: dict[tuple[str, str], tuple[str, str]] = {}
    for c in consolidations:
        for u in c.dependency_updates:
            key = (u.manifest_path, u.name)
            note = (u.note or "").strip()
            reason = (u.note_reason or "").strip()
            if not note:
                continue
            notes.setdefault(key, (note, reason))

    out: list[DependencyUpdate] = []
    for b in range_bumps:
        key = (b.manifest_path, b.name)
        note, reason = notes.get(key, ("", ""))
        out.append(
            DependencyUpdate(
                manifest_path=b.manifest_path, name=b.name,
                from_version=b.from_version, to_version=b.to_version,
                note=note, note_reason=reason,
            )
        )
    return out
