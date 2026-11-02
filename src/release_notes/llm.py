"""LLM calls for commit consolidation and release notes synthesis via giskard-agents."""

import json
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, override

from giskard.agents import add_prompts_path
from giskard.agents.generators import GiskardLLMGenerator
from giskard.agents.generators.base import GenerationParams
from giskard.llm import acompletion
from giskard.llm.translators import openai_chat
from giskard.llm.types import ChatMessage, CompletionResponse
from pydantic import BaseModel, Field

from .config import ProjectConfig
from .deps import dependency_updates_for_commit
from .models import (
    CommitDigest,
    ConsolidatedCommit,
    DependencyUpdate,
    GitHubIssue,
    LinearIssue,
    build_consolidated_commit_model,
    build_release_notes_payload_model,
)

LOG = logging.getLogger("release_notes.llm")

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PROMPTS_CONFIGURED = False
_TRANSLATOR_PATCHED = False
DEFAULT_MODEL = "azure_ai/gpt-5.6-luna"
DEFAULT_EFFORT = "high"


def _ensure_reasoning_effort_support() -> None:
    """Allow `reasoning_effort` to pass through giskard-llm's OpenAIChatTranslator."""
    global _TRANSLATOR_PATCHED
    if _TRANSLATOR_PATCHED:
        return
    orig_to_openai = openai_chat.OpenAIChatTranslator.to_openai
    openai_chat.KNOWN_COMPLETION_PARAMS = openai_chat.KNOWN_COMPLETION_PARAMS | {"reasoning_effort"}

    def _to_openai_with_effort(
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: Any = None,
        **params: Any,
    ) -> Any:
        effort = params.pop("reasoning_effort", None)
        out = orig_to_openai(model, messages, tools=tools, **params)
        if effort:
            out["reasoning_effort"] = effort
        return out

    openai_chat.OpenAIChatTranslator.to_openai = staticmethod(_to_openai_with_effort)
    _TRANSLATOR_PATCHED = True


class ReleaseNotesLLMGenerator(GiskardLLMGenerator):
    """GiskardLLMGenerator subclass that forwards `reasoning_effort` when configured."""

    reasoning_effort: str | None = Field(default=None)

    @override
    async def _call_model(
        self,
        messages: Sequence[ChatMessage],
        params: GenerationParams,
        metadata: dict[str, Any] | None = None,
    ) -> CompletionResponse:
        wire_params = params.model_dump(exclude={"tools"}, exclude_unset=True)
        wire_tools = self._serialize_tools(params.tools) if params.tools else []
        if wire_tools:
            wire_params["tools"] = wire_tools
        if metadata:
            wire_params["metadata"] = metadata
        if self.reasoning_effort:
            wire_params["reasoning_effort"] = self.reasoning_effort
        return await acompletion(messages=messages, model=self.model, **wire_params)


def ensure_azure_env(model: str) -> None:
    """Require Azure credentials when the model uses azure_ai/ or azure/."""
    if not (model.startswith("azure_ai/") or model.startswith("azure/")):
        return
    if not os.getenv("AZURE_AI_API_KEY"):
        raise SystemExit("Missing required environment variable: AZURE_AI_API_KEY")
    if not os.getenv("AZURE_AI_ENDPOINT"):
        api_base = os.getenv("AZURE_AI_API_BASE")
        if not api_base:
            raise SystemExit(
                "Missing required environment variable: AZURE_AI_ENDPOINT "
                "(or AZURE_AI_API_BASE as a fallback)"
            )
        os.environ["AZURE_AI_ENDPOINT"] = api_base


def _configure_prompts() -> None:
    global _PROMPTS_CONFIGURED
    if _PROMPTS_CONFIGURED:
        return
    add_prompts_path(_PROMPTS_DIR, "release-notes")
    _PROMPTS_CONFIGURED = True


def build_generator(
    *, model: str, reasoning_effort: str | None = DEFAULT_EFFORT
) -> ReleaseNotesLLMGenerator:
    _configure_prompts()
    _ensure_reasoning_effort_support()
    ensure_azure_env(model)
    return ReleaseNotesLLMGenerator(model=model, reasoning_effort=reasoning_effort)


def _linear_block(linear_issues: list[LinearIssue]) -> str:
    if not linear_issues:
        return ""
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
    return "\n".join(lines).rstrip() + "\n"


def _github_block(github_issues: list[GitHubIssue] | None) -> str:
    if not github_issues:
        return ""
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
    return "\n".join(lines).rstrip() + "\n"


async def consolidate_commit(
    commit_digest: CommitDigest,
    *,
    model: str,
    reasoning_effort: str | None = DEFAULT_EFFORT,
    linear_issues: list[LinearIssue],
    github_issues: list[GitHubIssue] | None = None,
    repo_root: Path,
    manifest_paths: tuple[str, ...],
    config: ProjectConfig | None = None,
) -> ConsolidatedCommit:
    """Consolidate a commit digest via giskard-agents structured output.

    Untrusted digest/issue text is interpolated through sandboxed Jinja (escaped).
    Canonical git fields are overwritten from parsed data after the LLM returns.
    """
    cfg = config or ProjectConfig()
    dep_updates = dependency_updates_for_commit(
        commit_digest, repo_root=repo_root, manifest_paths=manifest_paths
    )
    dep_updates_json = json.dumps([u.model_dump() for u in dep_updates], ensure_ascii=False)
    output_model = build_consolidated_commit_model(cfg)

    generator = build_generator(model=model, reasoning_effort=reasoning_effort)
    workflow = (
        generator.template("release-notes::commit_consolidation.j2")
        .with_inputs(
            commit_digest=commit_digest.digest,
            dependency_updates_json=dep_updates_json,
            linear_block=_linear_block(linear_issues),
            github_block=_github_block(github_issues),
            category_descriptions=cfg.commit_category_descriptions_for_llm(),
        )
        .with_output(output_model, strict=True, num_retries=2)
    )
    started = time.perf_counter()
    chat = await workflow.run()
    elapsed_s = time.perf_counter() - started
    LOG.info("Consolidated commit via LLM: sha=%s (%.2fs)", commit_digest.sha[:8], elapsed_s)

    payload: ConsolidatedCommit = chat.output

    allowed_deps = {(u.manifest_path, u.name, u.from_version, u.to_version): u for u in dep_updates}
    filtered_deps: list[DependencyUpdate] = []
    for u in payload.dependency_updates:
        key = (u.manifest_path, u.name, u.from_version, u.to_version)
        if key not in allowed_deps:
            continue
        filtered_deps.append(
            DependencyUpdate(
                manifest_path=u.manifest_path,
                name=u.name,
                from_version=u.from_version,
                to_version=u.to_version,
                note=(u.note or "").strip(),
                note_reason=(u.note_reason or "").strip(),
            )
        )

    return ConsolidatedCommit(
        sha=commit_digest.sha,
        subject=commit_digest.subject,
        pr_number=commit_digest.pr_number or 0,
        linear_keys=commit_digest.linear_keys,
        short_name=payload.short_name.strip(),
        category=payload.category,
        consolidated_description=payload.consolidated_description.strip(),
        why=payload.why.strip(),
        context=payload.context.strip(),
        upgrade_notes=[u.strip() for u in payload.upgrade_notes if u.strip()],
        unknowns=[u.strip() for u in payload.unknowns if u.strip()],
        dependency_updates=filtered_deps,
    )


def _consolidations_text(consolidations: list[ConsolidatedCommit]) -> str:
    lines: list[str] = []
    for c in consolidations:
        header = f"- {c.sha[:8]} {c.subject}"
        if c.pr_number:
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
    return "\n".join(lines).rstrip() + "\n"


async def synthesize_release_notes(
    consolidations: list[ConsolidatedCommit],
    *,
    model: str,
    reasoning_effort: str | None = DEFAULT_EFFORT,
    config: ProjectConfig,
) -> BaseModel:
    """Synthesize structured release notes; output shape follows config.sections."""
    payload_model = build_release_notes_payload_model(config)
    generator = build_generator(model=model, reasoning_effort=reasoning_effort)
    workflow = (
        generator.template("release-notes::release_notes_synthesis.j2")
        .with_inputs(
            consolidations_text=_consolidations_text(consolidations),
            section_descriptions=config.section_descriptions_for_llm(),
        )
        .with_output(payload_model, strict=True, num_retries=2)
    )
    started = time.perf_counter()
    chat = await workflow.run()
    elapsed_s = time.perf_counter() - started
    LOG.info("Synthesis via LLM completed in %.2fs", elapsed_s)
    return chat.output
