"""Write GitHub Actions step outputs without newline injection on scalars."""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

_SEMVER_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_BREAKING_HEADER_RE = re.compile(r"^[a-z]+(?:\([^)]+\))?!:")
_BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE: ")
_FEAT_HEADER_RE = re.compile(r"^feat(?:\([^)]+\))?:")
_PATCH_HEADER_RE = re.compile(r"^(?:fix|perf)(?:\([^)]+\))?:")
_DEPS_HEADER_RE = re.compile(r"^[a-z]+\(deps\):")
_BUMP_RANK = {"patch": 1, "minor": 2, "major": 3}


def sanitize_scalar(value: object) -> str:
    return str(value).replace("\r", "").replace("\n", "")


def multiline_delimiter(value: str) -> str:
    delimiter = "EOFMETA"
    while delimiter in value:
        delimiter = f"EOFMETA_{secrets.token_hex(8)}"
    return delimiter


def write_github_outputs(
    path: Path,
    *,
    scalars: dict[str, object] | None = None,
    multiline: dict[str, object] | None = None,
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in (scalars or {}).items():
            handle.write(f"{key}={sanitize_scalar(value)}\n")
        for key, value in (multiline or {}).items():
            text = "" if value is None else str(value)
            delimiter = multiline_delimiter(text)
            handle.write(f"{key}<<{delimiter}\n{text}\n{delimiter}\n")


def resolve_notes_output_path(output_path: str, version: str) -> str:
    cleaned_path = sanitize_scalar(output_path).strip()
    if cleaned_path:
        return cleaned_path
    cleaned_version = sanitize_scalar(version).strip()
    return f"release_notes/v{cleaned_version}.md"


def write_metadata_outputs(github_output: Path, metadata: dict) -> None:
    write_github_outputs(
        github_output,
        scalars={
            "notes_path": metadata.get("output_path") or "",
            "compare_url": metadata.get("compare_url") or "",
        },
        multiline={"summary": metadata.get("summary") or ""},
    )


def _parse_semver_tag(tag: str) -> tuple[int, int, int] | None:
    match = _SEMVER_TAG_RE.match(tag.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _git_lines(repo_root: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def commit_bump(message: str) -> str | None:
    """Return the SemVer bump a conventional commit asks for, or None if it releases nothing."""
    lines = message.strip().splitlines()
    if not lines:
        return None
    header = lines[0].strip()
    if _BREAKING_HEADER_RE.match(header) or any(
        _BREAKING_FOOTER_RE.match(line) for line in lines[1:]
    ):
        return "major"
    if _FEAT_HEADER_RE.match(header):
        return "minor"
    if _PATCH_HEADER_RE.match(header) or _DEPS_HEADER_RE.match(header):
        return "patch"
    return None


def _requested_release(
    repo_root: Path, requested_version: str, reachable_tags: list[tuple[tuple[int, int, int], str]]
) -> dict[str, str]:
    """Release the maintainer-requested version, if it is above every existing SemVer tag."""
    requested_tag = f"v{sanitize_scalar(requested_version).removeprefix('v')}"
    parsed = _parse_semver_tag(requested_tag)
    if parsed is None:
        raise SystemExit(
            f"::error::Requested release version {requested_version!r} is not a X.Y.Z version."
        )
    all_tags = sorted(
        (parsed_tag, tag)
        for tag in _git_lines(repo_root, "tag", "--list", "v*")
        if (parsed_tag := _parse_semver_tag(tag)) is not None
    )
    if all_tags and parsed <= all_tags[-1][0]:
        raise SystemExit(
            f"::error::Requested release version {requested_tag} must be higher than "
            f"{all_tags[-1][1]}, the latest SemVer tag."
        )
    return {
        "should_release": "true",
        "tag": requested_tag,
        "previous_tag": max(reachable_tags)[1] if reachable_tags else "",
    }


def compute_next_release_tag(repo_root: Path, requested_version: str = "") -> dict[str, str]:
    requested_version = requested_version.strip()
    head_tags_out = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    head_semver_tags = [
        (parsed, tag.strip())
        for tag in head_tags_out.splitlines()
        if (parsed := _parse_semver_tag(tag)) is not None
    ]
    if head_semver_tags:
        head_semver_tags.sort(key=lambda item: item[0])
        existing_tag = head_semver_tags[-1][1]
        if requested_version:
            raise SystemExit(
                f"::error::HEAD is already released as {existing_tag}; merge a new commit "
                "before requesting a release version."
            )
        return {
            "should_release": "false",
            "tag": existing_tag,
            "previous_tag": existing_tag,
        }

    semver_tags = [
        (parsed, tag)
        for tag in _git_lines(repo_root, "tag", "--merged", "HEAD")
        if (parsed := _parse_semver_tag(tag)) is not None
    ]
    if requested_version:
        return _requested_release(repo_root, requested_version, semver_tags)
    if not semver_tags:
        unreachable = [
            tag for tag in _git_lines(repo_root, "tag", "--list", "v*") if _parse_semver_tag(tag)
        ]
        if unreachable:
            raise SystemExit(
                "::error::No SemVer tag is reachable from HEAD, but these exist: "
                f"{', '.join(sorted(unreachable))}. Fetch full history with tags "
                "(fetch-depth: 0) or fix the branch."
            )
        raise SystemExit(
            "::error::No SemVer tag exists yet. Run the release workflow manually with a "
            "version input to cut the first release."
        )

    semver_tags.sort(key=lambda item: item[0])
    (major, minor, patch), prev_tag = semver_tags[-1]

    log_out = subprocess.run(
        ["git", "log", f"{prev_tag}..HEAD", "--format=%B%x00"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    bumps = [bump for msg in log_out.split("\x00") if (bump := commit_bump(msg))]
    if not bumps:
        return {"should_release": "false", "tag": prev_tag, "previous_tag": prev_tag}
    bump = max(bumps, key=_BUMP_RANK.__getitem__)
    if bump == "major" and major == 0:
        bump = "minor"

    if bump == "major":
        next_tag = f"v{major + 1}.0.0"
    elif bump == "minor":
        next_tag = f"v{major}.{minor + 1}.0"
    else:
        next_tag = f"v{major}.{minor}.{patch + 1}"

    return {
        "should_release": "true",
        "tag": next_tag,
        "previous_tag": prev_tag,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", required=True)
    parser.add_argument("--metadata-file")
    parser.add_argument(
        "--resolve-output-path",
        action="store_true",
        help="Write sanitized composite output path from OUTPUT_PATH/VERSION env vars",
    )
    parser.add_argument(
        "--compute-next-release-tag",
        action="store_true",
        help="Compute next SemVer Umbrella Release tag from git history",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory for --compute-next-release-tag",
    )
    parser.add_argument(
        "--release-version",
        default="",
        help="X.Y.Z to release instead of the computed bump (--compute-next-release-tag only)",
    )
    args = parser.parse_args(argv)
    github_output = Path(args.github_output)

    if args.compute_next_release_tag:
        write_github_outputs(
            github_output,
            scalars=compute_next_release_tag(Path(args.repo_root), args.release_version),
        )
        return 0

    if args.resolve_output_path:
        write_github_outputs(
            github_output,
            scalars={
                "output": resolve_notes_output_path(
                    os.environ.get("INPUT_OUTPUT_PATH", ""),
                    os.environ.get("INPUT_VERSION", ""),
                )
            },
        )
        return 0

    if not args.metadata_file:
        print("error: --metadata-file is required unless --resolve-output-path", file=sys.stderr)
        return 2
    metadata = json.loads(Path(args.metadata_file).read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise SystemExit("metadata JSON must be an object")
    write_metadata_outputs(github_output, metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
