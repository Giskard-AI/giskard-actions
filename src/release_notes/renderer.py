"""Markdown rendering for release notes."""

from datetime import datetime, timezone

from .models import (
    CommitDigest,
    ConsolidatedCommit,
    DependencyUpdate,
    LinearIssue,
    ReleaseNoteItem,
    ReleaseNotesPayload,
    TagRange,
)


class MarkdownWriter:
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
    payload: ReleaseNotesPayload,
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
    include_digests_in_spoilers: bool,
    dependency_bumps: list[DependencyUpdate],
) -> str:
    ts = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    w = MarkdownWriter()

    w.h1(f"Release notes v{version}")
    w.bullet(f"Range: `{tag_range.previous_tag}..{tag_range.current_tag}`")
    if compare_url:
        w.bullet(f"Compare: {compare_url}")
    w.bullet(f"Generated: {ts}")
    w.blank()

    w.h2("Summary")
    for it in payload.summary:
        name = it.short_name.strip()
        desc = it.description.strip()
        if name:
            w.raw(f"**{name}**")
            w.blank()
        w.p(desc)

    w.h2("What changed")

    def section(title: str, items: list[ReleaseNoteItem]) -> None:
        if not items:
            return
        w.h3(title)
        for it in items:
            w.bullet_kv(it.short_name, it.description)
        w.blank()

    section("Features", payload.features)
    section("Fixes", payload.fixes)
    section("UX / Frontend", payload.ux_frontend)
    section("API / Backend", payload.api_backend)
    section("Infra / Deps", payload.infra_deps)

    w.h2("Upgrade notes")
    if payload.upgrade_notes:
        for it in payload.upgrade_notes:
            w.bullet_kv(it.short_name, it.description)
        w.blank()
    else:
        w.bullet("No explicit upgrade notes detected from the diff digest.")
        w.blank()

    w.h2("Appendix")

    if dependency_bumps:
        w.h3("Dependencies")
        by_manifest: dict[str, list[DependencyUpdate]] = {}
        for b in dependency_bumps:
            by_manifest.setdefault(b.manifest_path, []).append(b)
        for mp in sorted(by_manifest):
            w.raw(f"#### `{mp}`")
            w.blank()
            for b in sorted(by_manifest[mp], key=lambda x: x.name.lower()):
                note = f" — {b.note.strip()}" if b.note and b.note.strip() else ""
                w.bullet(f"{b.name}: {b.from_version} → {b.to_version}{note}")
            w.blank()

    w.h3("Diff stats")
    w.codeblock("", (diff_stat.rstrip() or "<empty>"))
    w.h3("Commits")
    w.codeblock("", (commit_list.rstrip() or "<empty>"))

    w.h3("Consolidated commits")
    by_sha: dict[str, ConsolidatedCommit] = {c.sha: c for c in consolidations}
    for cd in commit_digests:
        cons = by_sha.get(cd.sha)
        summary = f"{cd.sha[:8]} {cd.subject}"
        if cd.pr_number is not None:
            summary += f" (#{cd.pr_number})"

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

            body.append("**Changed files**:")
            if cd.changed_files:
                for p in cd.changed_files:
                    body.append(f"- `{p}`")
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

    return w.finish()
