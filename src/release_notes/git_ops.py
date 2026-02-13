"""Git operations: tag detection, commit listing, diff extraction."""

import logging
import re
import subprocess
import time
from pathlib import Path

from .models import CommitDigest, CommitInfo, TagRange

LOG = logging.getLogger("release_notes.git")


def run_git(args: list[str], repo_root: Path) -> str:
    cmd = ["git", *args]
    started = time.perf_counter()
    p = subprocess.run(
        cmd,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed_s = time.perf_counter() - started
    if p.returncode != 0:
        raise SystemExit(
            f"git command failed:\n- cmd: {' '.join(cmd)}\n"
            f"- exit: {p.returncode}\n- stderr: {p.stderr.strip()}"
        )
    LOG.debug("git ok (%.2fs): %s", elapsed_s, " ".join(cmd))
    return p.stdout


def git_show(ref: str, path: str, *, repo_root: Path) -> str | None:
    p = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        return None
    return p.stdout


def validate_version(version: str) -> None:
    if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*$", version):
        raise SystemExit(f"Invalid version: {version!r}. Expected X.Y.Z[...]")


def _ensure_tag_exists(tag: str, repo_root: Path) -> None:
    run_git(["rev-parse", "--verify", tag], repo_root=repo_root)


def _detect_previous_tag(current_tag: str, repo_root: Path) -> str:
    tags = run_git(["tag", "--list", "v*.*.*", "--sort=v:refname"], repo_root=repo_root)
    ordered = [t.strip() for t in tags.splitlines() if t.strip()]
    if current_tag not in ordered:
        raise SystemExit(
            f"Current tag {current_tag!r} not found in repository tags "
            f"(found {len(ordered)} v*.*.* tags)."
        )
    idx = ordered.index(current_tag)
    if idx == 0:
        raise SystemExit(f"Current tag {current_tag!r} is the first tag; cannot auto-detect previous.")
    return ordered[idx - 1]


def detect_tag_range(
    version: str, previous_tag_override: str | None, repo_root: Path
) -> TagRange:
    validate_version(version)
    current_tag = f"v{version}"
    LOG.info("Detecting tag range for version=%s (current tag=%s)", version, current_tag)
    _ensure_tag_exists(current_tag, repo_root=repo_root)

    if previous_tag_override:
        previous_tag = previous_tag_override.strip()
        LOG.info("Using --previous-tag override: %s", previous_tag)
        _ensure_tag_exists(previous_tag, repo_root=repo_root)
    else:
        previous_tag = _detect_previous_tag(current_tag=current_tag, repo_root=repo_root)
        LOG.info("Auto-detected previous tag: %s", previous_tag)

    return TagRange(previous_tag=previous_tag, current_tag=current_tag)


def list_commits(tag_range: TagRange, repo_root: Path) -> list[CommitInfo]:
    raw = run_git(
        [
            "log", "--no-merges", "--reverse",
            "--pretty=format:%H%x1f%s%x1f%b%x1e",
            f"{tag_range.previous_tag}..{tag_range.current_tag}",
        ],
        repo_root=repo_root,
    )
    commits: list[CommitInfo] = []
    for rec in raw.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        subject = parts[1].strip()
        body = parts[2].rstrip() if len(parts) >= 3 else ""
        commits.append(CommitInfo(sha=sha, subject=subject, body=body))
    return commits


def extract_linear_keys(text: str) -> list[str]:
    keys = re.findall(r"\[([A-Z]+-\d+)\]", text or "")
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def extract_github_issue_numbers(text: str) -> list[int]:
    """Extract GitHub issue/PR references like #123 from text.

    Excludes the PR number that appears at the end of the subject line
    (e.g. "(#456)") since that's handled separately by parse_pr_number.
    Captures references in commit body like "Fixes #123", "closes #456", etc.
    """
    # Find all #N references
    numbers = re.findall(r"(?<!\()#(\d+)(?!\))", text or "")
    # Also find references in "Fixes #123" / "Closes #123" style (even with parens)
    numbers += re.findall(r"(?:fix(?:es)?|close[sd]?|resolve[sd]?)\s+#(\d+)", text or "", re.IGNORECASE)
    seen: set[int] = set()
    out: list[int] = []
    for n in numbers:
        num = int(n)
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


def parse_pr_number(subject: str) -> int | None:
    m = re.search(r"\(#(\d+)\)\s*$", subject or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8", errors="replace"))


def _truncate(s: str, max_bytes: int) -> tuple[str, bool]:
    b = s.encode("utf-8", errors="replace")
    if len(b) <= max_bytes:
        return s, False
    cut = b[: max_bytes - 50].decode("utf-8", errors="replace")
    return f"{cut}\n\n...[TRUNCATED]...\n", True


def _matches_any(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(p) for p in prefixes)


def _filter_name_status(raw: str, *, ignored_basenames: set[str]) -> str:
    out: list[str] = []
    for ln in raw.splitlines():
        ln = ln.rstrip("\n")
        if not ln.strip():
            continue
        parts = re.split(r"\s+", ln, maxsplit=2)
        if len(parts) < 2:
            out.append(ln)
            continue
        path = parts[-1]
        if Path(path).name in ignored_basenames:
            continue
        out.append(ln)
    return "\n".join(out) + ("\n" if out else "")


def _filter_diff_stat(raw: str, *, ignored_basenames: set[str]) -> str:
    out: list[str] = []
    for ln in raw.splitlines():
        ln = ln.rstrip("\n")
        if not ln.strip():
            continue
        if "|" not in ln:
            out.append(ln)
            continue
        path_part = ln.split("|", 1)[0].strip()
        if Path(path_part).name in ignored_basenames:
            continue
        out.append(ln)
    return "\n".join(out) + ("\n" if out else "")


def git_diff_stat(
    range_ref: str, *, repo_root: Path, ignored_basenames: set[str]
) -> str:
    raw = run_git(
        ["diff", "--stat", "--stat-name-width=200", "--stat-width=240", range_ref],
        repo_root=repo_root,
    )
    return _filter_diff_stat(raw, ignored_basenames=ignored_basenames)


def _score_path(path: str, high_signal_prefixes: tuple[str, ...]) -> int:
    for i, prefix in enumerate(high_signal_prefixes):
        if path.startswith(prefix):
            return i
    return 50


def build_commit_digest(
    commit: CommitInfo,
    repo_root: Path,
    *,
    ignored_basenames: set[str],
    max_total_bytes: int,
    max_file_bytes: int,
    high_signal_prefixes: tuple[str, ...],
) -> CommitDigest:
    sha = commit.sha
    subject = commit.subject
    pr_number = parse_pr_number(subject)
    full_text = subject + "\n" + (commit.body or "")
    linear_keys = extract_linear_keys(full_text)
    github_issue_numbers = extract_github_issue_numbers(commit.body or "")
    # Exclude the PR number itself from issue references
    if pr_number and pr_number in github_issue_numbers:
        github_issue_numbers = [n for n in github_issue_numbers if n != pr_number]

    parts: list[str] = []
    commit_range = f"{sha}^..{sha}"

    def add_block(title: str, content: str, *, force: bool = False) -> None:
        header = f"\n## {title}\n"
        block = header + content.rstrip() + "\n"
        if force:
            parts.append(block)
            return
        current_size = _byte_len("".join(parts))
        if current_size + _byte_len(block) <= max_total_bytes:
            parts.append(block)

    add_block("Commit", f"sha={sha}\nsubject={subject}\n", force=True)
    if commit.body.strip():
        body_t, was_truncated = _truncate(commit.body, 10_000)
        suffix = " (TRUNCATED)" if was_truncated else ""
        add_block(f"Commit body{suffix}", body_t)

    name_status_raw = run_git(["diff", "--name-status", commit_range], repo_root=repo_root)
    add_block(
        "Name status",
        _filter_name_status(name_status_raw, ignored_basenames=ignored_basenames) or "<empty>",
        force=True,
    )
    add_block(
        "Diff stat",
        git_diff_stat(commit_range, repo_root=repo_root, ignored_basenames=ignored_basenames)
        or "<empty>",
    )

    changed_files_raw = run_git(["diff", "--name-only", commit_range], repo_root=repo_root)
    changed_files = [p.strip() for p in changed_files_raw.splitlines() if p.strip()]
    changed_files = [p for p in changed_files if Path(p).name not in ignored_basenames]

    selected = [p for p in changed_files if _matches_any(p, high_signal_prefixes)]
    selected_sorted = sorted(
        selected, key=lambda p: _score_path(p, high_signal_prefixes)
    )

    add_block(
        "Selected file diffs (truncated per-file, capped total)",
        "\n".join(f"- {p}" for p in selected_sorted) or "<none>",
        force=True,
    )

    for path in selected_sorted:
        diff = run_git(["diff", "-U1", commit_range, "--", path], repo_root=repo_root)
        diff_t, was_truncated = _truncate(diff, max_file_bytes)
        suffix = " (TRUNCATED)" if was_truncated else ""
        add_block(f"Diff: {path}{suffix}", diff_t)

    digest = "".join(parts).strip() + "\n"
    return CommitDigest(
        sha=sha,
        subject=subject,
        pr_number=pr_number,
        linear_keys=linear_keys,
        github_issue_numbers=github_issue_numbers,
        changed_files=changed_files,
        selected_files=selected_sorted,
        digest=digest,
    )
