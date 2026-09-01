"""Markdown rendering for release notes."""

from datetime import UTC, datetime

from pydantic import BaseModel

from .config import ProjectConfig
from .models import (
    CommitDigest,
    ConsolidatedCommit,
    DependencyUpdate,
    GitHubIssue,
    LinearIssue,
    TagRange,
    payload_items,
)


class MarkdownWriter:
    """Accumulates markdown lines and exposes helpers for headings, bullets, etc.

    Call `finish()` to get the final string with a single trailing newline.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []

    def raw(self, line: str = "") -> None:
        self._lines.append(line)

    def blank(self) -> None:
        if not self._lines:
            self._lines.append("")
            return
        if self._lines[-1] != "":
            self._lines.append("")

    def h1(self, text: str) -> None:
        self.raw(f"# {text}")
        self.blank()

    def h2(self, text: str) -> None:
        self.raw(f"## {text}")
        self.blank()

    def h3(self, text: str) -> None:
        self.raw(f"### {text}")
        self.blank()

    def h4(self, text: str) -> None:
        self.raw(f"#### {text}")
        self.blank()

    def p(self, text: str) -> None:
        for ln in (text or "").splitlines() or [""]:
            self.raw(ln)
        self.blank()

    def bullet(self, text: str) -> None:
        self.raw(f"- {text}")

    def bullet_kv(self, key: str, value: str) -> None:
        k = (key or "").strip()
        v = (value or "").strip()
        if k:
            self.bullet(f"**{k}**: {v}")
        else:
            self.bullet(v)

    def codeblock(self, lang: str, text: str) -> None:
        self.raw(f"```{lang}")
        for ln in (text or "").splitlines():
            self.raw(ln)
        self.raw("```")
        self.blank()

    def details(self, summary: str, body_lines: list[str]) -> None:
        self.raw("<details>")
        self.raw(f"<summary><code>{summary}</code></summary>")
        self.blank()
        for ln in body_lines:
            self.raw(ln)
        self.blank()
        self.raw("</details>")
        self.blank()

    def finish(self) -> str:
        return "\n".join(self._lines).rstrip() + "\n"


def render_markdown(
    payload: BaseModel,
    tag_range: TagRange,
    *,
    version: str,
    generated_at: datetime,
    compare_url: str | None,
    diff_stat: str,
    commit_list: str,
    commit_digests: list[CommitDigest],
    consolidations: list[ConsolidatedCommit],
    linear_issues_by_key: dict[str, LinearIssue],
    github_issues_by_number: dict[int, GitHubIssue],
    include_digests_in_spoilers: bool,
    dependency_bumps: list[DependencyUpdate],
    file_diff_results: dict[str, str],
    config: ProjectConfig,
) -> str:
    """Render the full release notes document as a markdown string.

    Sections are rendered in order according to config.enabled_section_ids().
    file_diff_results maps repo-relative paths to their raw unified diff (or ''
    for no changes) and is rendered verbatim after upgrade notes.
    """
    ts = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    w = MarkdownWriter()
    enabled = config.enabled_section_ids()

    w.h1(f"Release notes v{version}")
    w.bullet(f"Range: `{tag_range.previous_tag}..{tag_range.current_tag}`")
    if compare_url:
        w.bullet(f"Compare: {compare_url}")
    w.bullet(f"Generated: {ts}")
    w.blank()

    # Summary
    if "summary" in enabled:
        w.h2(config.section_title("summary"))
        for it in payload_items(payload, "summary"):
            name = it.short_name.strip()
            desc = it.description.strip()
            if name:
                w.raw(f"**{name}**")
                w.blank()
            w.p(desc)

    # What changed (sub-sections from config, excluding summary/upgrade/deps/appendix)
    what_changed_ids = config.what_changed_section_ids()
    if what_changed_ids:
        w.h2("What changed")

        def section(section_id: str) -> None:
            items = payload_items(payload, section_id)
            if not items:
                return
            w.h3(config.section_title(section_id))
            for it in items:
                w.bullet_kv(it.short_name, it.description)
            w.blank()

        for sid in what_changed_ids:
            section(sid)

    # Upgrade notes
    if "upgrade_notes" in enabled:
        w.h2(config.section_title("upgrade_notes"))
        upgrade_items = payload_items(payload, "upgrade_notes")
        if upgrade_items:
            for it in upgrade_items:
                w.bullet_kv(it.short_name, it.description)
            w.blank()
        else:
            w.bullet("No explicit upgrade notes detected from the diff digest.")
            w.blank()

    # File diffs (verbatim diff for tracked paths, e.g. helm values.yaml)
    for path, diff_text in file_diff_results.items():
        w.h2(f"`{path}` diff")
        if diff_text.strip():
            w.codeblock("diff", diff_text.rstrip())
        else:
            w.p(f"_No changes in `{path}`._")

    # Dependencies — standalone section only when appendix is not also enabled.
    # When appendix is enabled, deps are rendered inside it (see below).
    if "deps" in enabled and dependency_bumps and "appendix" not in enabled:
        w.h2(config.section_title("deps"))
        _render_deps(w, dependency_bumps, config)

    # Appendix
    if "appendix" in enabled:
        w.h2(config.section_title("appendix"))

        # Dependencies as the first subsection of the appendix
        if "deps" in enabled and dependency_bumps:
            w.h3(config.section_title("deps"))
            _render_deps(w, dependency_bumps, config)

        w.h3("Diff stats")
        w.codeblock("", (diff_stat.rstrip() or "<empty>"))
        w.h3("Commits")
        w.codeblock("", (commit_list.rstrip() or "<empty>"))

        w.h3("Consolidated commits")
        _render_consolidated_commits(
            w,
            commit_digests=commit_digests,
            consolidations=consolidations,
            linear_issues_by_key=linear_issues_by_key,
            github_issues_by_number=github_issues_by_number,
            include_digests_in_spoilers=include_digests_in_spoilers,
        )

    return w.finish()


def _render_deps(
    w: MarkdownWriter,
    dependency_bumps: list[DependencyUpdate],
    config: "ProjectConfig",
) -> None:
    """Render dependency bump table grouped by manifest path."""
    by_manifest: dict[str, list[DependencyUpdate]] = {}
    for b in dependency_bumps:
        by_manifest.setdefault(b.manifest_path, []).append(b)
    for mp in sorted(by_manifest):
        w.h4(f"`{mp}`")
        for b in sorted(by_manifest[mp], key=lambda x: x.name.lower()):
            note = f" — {b.note.strip()}" if b.note and b.note.strip() else ""
            w.bullet(f"{b.name}: {b.from_version} → {b.to_version}{note}")
        w.blank()


def _render_consolidated_commits(
    w: MarkdownWriter,
    *,
    commit_digests: list[CommitDigest],
    consolidations: list[ConsolidatedCommit],
    linear_issues_by_key: dict[str, LinearIssue],
    github_issues_by_number: dict[int, GitHubIssue],
    include_digests_in_spoilers: bool,
) -> None:
    """Render per-commit collapsible <details> blocks for the appendix.

    Each block includes category, Linear/GitHub links, changed files, why/context,
    consolidated description, upgrade notes, dependency updates, and unknowns.
    """
    by_sha: dict[str, ConsolidatedCommit] = {c.sha: c for c in consolidations}
    for cd in commit_digests:
        cons = by_sha.get(cd.sha)
        # subject already contains the PR number when parsed from "(#NNN)" at the end
        summary = f"{cd.sha[:8]} {cd.subject}"

        body: list[str] = []
        if cons is not None:
            body.append(f"**Category**: {cons.category}")
            body.append("")
            if cons.linear_keys:
                body.append("**Linear**:")
                for k in cons.linear_keys:
                    iss = linear_issues_by_key.get(k)
                    if iss and iss.url:
                        title = iss.title.strip() or k
                        body.append(f"- [{k}]({iss.url}) — {title}")
                    else:
                        body.append(f"- {k}")
                body.append("")

            # GitHub issues/PRs referenced by this commit
            gh_nums = cd.github_issue_numbers[:]
            if cd.pr_number and cd.pr_number not in gh_nums:
                gh_nums.insert(0, cd.pr_number)
            gh_items = [github_issues_by_number[n] for n in gh_nums if n in github_issues_by_number]
            if gh_items:
                body.append("**GitHub**:")
                for gh in gh_items:
                    kind = "PR" if gh.is_pull_request else "Issue"
                    body.append(f"- [#{gh.number}]({gh.url}) ({kind}) — {gh.title}")
                body.append("")

            body.append("**Changed files**:")
            # Prefer high-signal selected files; fall back to all changed files.
            # Cap at _MAX_SHOWN_FILES to avoid bloating large refactor commits.
            _MAX_SHOWN_FILES = 20
            display = cd.selected_files if cd.selected_files else cd.changed_files
            shown = display[:_MAX_SHOWN_FILES]
            extra = len(cd.changed_files) - len(shown)
            if shown:
                for p in shown:
                    body.append(f"- `{p}`")
                if extra > 0:
                    body.append(f"- _...and {extra} more files_")
            else:
                body.append("- <none>")
            body.append("")

            if cons.why:
                body.append(f"**Why**: {cons.why.strip()}")
            if cons.context:
                body.append("")
                body.append("**Context**:")
                body.append("")
                body.append(cons.context.strip())
            body.append("")
            body.append("**Consolidated description**:")
            body.append("")
            prefix = (cons.short_name.strip() + ": ") if cons.short_name.strip() else ""
            body.append(prefix + (cons.consolidated_description.strip() or "<empty>"))
            body.append("")

            if cons.upgrade_notes:
                body.append("**Upgrade notes**:")
                for u in cons.upgrade_notes:
                    body.append(f"- {u}")
                body.append("")
            if cons.dependency_updates:
                body.append("**Dependency updates**:")
                for du in cons.dependency_updates:
                    note = f" — {du.note.strip()}" if du.note and du.note.strip() else ""
                    body.append(
                        f"- `{du.manifest_path}` {du.name}: "
                        f"{du.from_version} → {du.to_version}{note}"
                    )
                body.append("")
            if cons.unknowns:
                body.append("**Unknowns**:")
                for u in cons.unknowns:
                    body.append(f"- {u}")
                body.append("")
        else:
            body.append("_No consolidation available for this commit._")
            body.append("")

        if include_digests_in_spoilers:
            body.append("**Commit digest (debug)**:")
            body.append("")
            body.append("```")
            body.append(cd.digest.rstrip())
            body.append("```")
            body.append("")

        w.details(summary, body)
