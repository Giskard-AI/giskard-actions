"""LLM calls for commit consolidation and release notes synthesis."""

import json
import logging
import time
from pathlib import Path

import jinja2
import litellm

from .deps import dependency_updates_for_commit
from .models import (
    CommitDigest,
    ConsolidatedCommit,
    DependencyUpdate,
    GitHubIssue,
    LinearIssue,
    ReleaseNotesPayload,
)

LOG = logging.getLogger("release_notes.llm")

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _render_messages(template_name: str, **kwargs: str) -> list[dict[str, str]]:
    """Render a prompt template into a list of chat messages.

    Templates use {% message role %} ... {% endmessage %} blocks to delimit
    chat messages.  We first split on these markers (which are NOT real Jinja2
    tags), then render each block's content through Jinja2 for variable
    substitution.
    """
    import re

    raw = (_PROMPTS_DIR / template_name).read_text(encoding="utf-8")

    env = jinja2.Environment(autoescape=False, undefined=jinja2.StrictUndefined)

    messages: list[dict[str, str]] = []
    # Split on {% message <role> %} markers
    parts = re.split(r"\{%\s*message\s+(\w+)\s*%\}", raw)
    # parts[0] = before first message (usually empty)
    # parts[1] = role, parts[2] = content, parts[3] = role, ...
    i = 1
    while i < len(parts):
        role = parts[i].strip()
        content_raw = parts[i + 1] if i + 1 < len(parts) else ""
        # Remove {% endmessage %} tags
        content_raw = re.sub(r"\{%\s*endmessage\s*%\}", "", content_raw)
        # Render Jinja2 variables ({{ var }}) and conditionals ({% if %})
        content = env.from_string(content_raw).render(**kwargs).strip()
        if content:
            messages.append({"role": role, "content": content})
        i += 2

    return messages


def _add_additional_properties_false(schema: dict) -> dict:
    """Recursively add additionalProperties: false to all object schemas (required by Azure)."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        for value in schema.values():
            if isinstance(value, dict):
                _add_additional_properties_false(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _add_additional_properties_false(item)
    return schema


def _call_llm_structured(
    messages: list[dict[str, str]],
    *,
    model: str,
    response_model: type,
    temperature: float = 0.2,
    num_retries: int = 2,
) -> dict:
    """Call LLM with structured JSON output using litellm."""
    schema = _add_additional_properties_false(response_model.model_json_schema())

    for attempt in range(1 + num_retries):
        try:
            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception:
            if attempt < num_retries:
                LOG.warning("LLM call attempt %d failed, retrying...", attempt + 1)
                continue
            raise


async def consolidate_commit(
    commit_digest: CommitDigest,
    *,
    model: str,
    linear_issues: list[LinearIssue],
    github_issues: list[GitHubIssue] | None = None,
    repo_root: Path,
    manifest_paths: tuple[str, ...],
) -> ConsolidatedCommit:
    linear_block = ""
    if linear_issues:
        lines = ["Linear issues referenced by this commit:", ""]
        for iss in linear_issues:
            lines.append(f"- {iss.identifier}: {iss.title}")
            if iss.url:
                lines.append(f"  URL: {iss.url}")
            if iss.description.strip():
                lines.append("  Description:")
                lines.append("  -----")
                for ln in iss.description.rstrip().splitlines():
                    lines.append(f"  {ln}")
                lines.append("  -----")
        linear_block = "\n".join(lines).rstrip() + "\n"

    github_block = ""
    if github_issues:
        lines = ["GitHub issues/PRs referenced by this commit:", ""]
        for gh in github_issues:
            kind = "PR" if gh.is_pull_request else "Issue"
            lines.append(f"- #{gh.number} ({kind}): {gh.title}")
            if gh.url:
                lines.append(f"  URL: {gh.url}")
            if gh.labels:
                lines.append(f"  Labels: {', '.join(gh.labels)}")
            if gh.body and gh.body.strip():
                lines.append("  Body:")
                lines.append("  -----")
                for ln in gh.body.rstrip().splitlines():
                    lines.append(f"  {ln}")
                lines.append("  -----")
        github_block = "\n".join(lines).rstrip() + "\n"

    dep_updates = dependency_updates_for_commit(
        commit_digest, repo_root=repo_root, manifest_paths=manifest_paths
    )
    dep_updates_json = json.dumps([u.model_dump() for u in dep_updates], ensure_ascii=False)

    # Build the output schema description for the template
    output_schema = json.dumps(ConsolidatedCommit.model_json_schema(), indent=2)

    messages = _render_messages(
        "commit_consolidation.j2",
        commit_digest=commit_digest.digest,
        dependency_updates_json=dep_updates_json,
        linear_block=linear_block,
        github_block=github_block,
        _instr_output=output_schema,
    )

    started = time.perf_counter()
    raw = _call_llm_structured(messages, model=model, response_model=ConsolidatedCommit)
    elapsed_s = time.perf_counter() - started
    LOG.info("Consolidated commit via LLM: sha=%s (%.2fs)", commit_digest.sha[:8], elapsed_s)

    payload = ConsolidatedCommit.model_validate(raw)

    # Enforce canonical fields from git parsing
    allowed_deps = {
        (u.manifest_path, u.name, u.from_version, u.to_version): u for u in dep_updates
    }
    filtered_deps: list[DependencyUpdate] = []
    for u in payload.dependency_updates:
        key = (u.manifest_path, u.name, u.from_version, u.to_version)
        if key not in allowed_deps:
            continue
        filtered_deps.append(
            DependencyUpdate(
                manifest_path=u.manifest_path, name=u.name,
                from_version=u.from_version, to_version=u.to_version,
                note=(u.note or "").strip() or None,
                note_reason=(u.note_reason or "").strip() or None,
            )
        )

    return ConsolidatedCommit(
        sha=commit_digest.sha,
        subject=commit_digest.subject,
        pr_number=commit_digest.pr_number,
        linear_keys=commit_digest.linear_keys,
        short_name=payload.short_name.strip(),
        category=payload.category,
        consolidated_description=payload.consolidated_description.strip(),
        why=(payload.why or "").strip() or None,
        context=(payload.context or "").strip() or None,
        upgrade_notes=[u.strip() for u in payload.upgrade_notes if u.strip()],
        unknowns=[u.strip() for u in payload.unknowns if u.strip()],
        dependency_updates=filtered_deps,
    )


async def synthesize_release_notes(
    consolidations: list[ConsolidatedCommit], *, model: str
) -> ReleaseNotesPayload:
    lines: list[str] = []
    for c in consolidations:
        header = f"- {c.sha[:8]} {c.subject}"
        if c.pr_number is not None:
            header += f" (#{c.pr_number})"
        lines.append(header)
        lines.append(f"  Category: {c.category}")
        lines.append(f"  Short name: {c.short_name}")
        if c.linear_keys:
            lines.append(f"  Linear: {', '.join(c.linear_keys)}")
        if c.why:
            lines.append(f"  Why: {c.why}")
        if c.context:
            lines.append("  Context:")
            for ln in c.context.strip().splitlines():
                lines.append(f"    {ln}")
        lines.append("  Consolidated:")
        for ln in c.consolidated_description.strip().splitlines():
            lines.append(f"    {ln}")
        if c.upgrade_notes:
            lines.append("  Upgrade notes:")
            for u in c.upgrade_notes:
                lines.append(f"    - {u}")
        if c.unknowns:
            lines.append("  Unknowns:")
            for u in c.unknowns:
                lines.append(f"    - {u}")
        lines.append("")
    consolidations_text = "\n".join(lines).rstrip() + "\n"

    output_schema = json.dumps(ReleaseNotesPayload.model_json_schema(), indent=2)

    messages = _render_messages(
        "release_notes_synthesis.j2",
        consolidations_text=consolidations_text,
        _instr_output=output_schema,
    )

    started = time.perf_counter()
    raw = _call_llm_structured(messages, model=model, response_model=ReleaseNotesPayload)
    elapsed_s = time.perf_counter() - started
    LOG.info("Synthesis via LLM completed in %.2fs", elapsed_s)
    return ReleaseNotesPayload.model_validate(raw)
