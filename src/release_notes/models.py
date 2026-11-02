"""Pydantic models and dataclasses used across the release notes pipeline."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from .config import ProjectConfig


class CommitCategory(StrEnum):
    FEATURE = "feature"
    FIX = "fix"
    UX = "ux"
    API = "api"
    INFRA = "infra"
    UPGRADE = "upgrade"
    OTHER = "other"


@dataclass(frozen=True)
class TagRange:
    previous_tag: str
    current_tag: str


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    body: str


@dataclass(frozen=True)
class LinearIssue:
    identifier: str
    title: str
    url: str
    description: str


@dataclass(frozen=True)
class GitHubIssue:
    """A GitHub issue or pull request fetched via the REST API."""

    number: int
    title: str
    url: str
    body: str
    is_pull_request: bool
    state: str  # "open" or "closed"
    labels: list[str]


@dataclass(frozen=True)
class CommitDigest:
    sha: str
    subject: str
    pr_number: int | None
    linear_keys: list[str]
    github_issue_numbers: list[int]
    changed_files: list[str]
    selected_files: list[str]
    digest: str


class DependencyUpdate(BaseModel):
    manifest_path: str = Field(description="Repo-relative manifest path.")
    name: str = Field(description="Dependency name.")
    from_version: str = Field(description="Previous version/spec.")
    to_version: str = Field(description="New version/spec.")
    note: str = Field(default="", description="Optional note if non-routine, empty string if none.")
    note_reason: str = Field(
        default="", description="Reason: security/breaking/compat/etc., empty string if none."
    )


class ConsolidatedCommit(BaseModel):
    sha: str = Field(default="", description="Full git SHA.")
    subject: str = Field(default="", description="Commit subject line.")
    pr_number: int = Field(default=0, description="PR number from subject like (#1234), 0 if none.")
    linear_keys: list[str] = Field(default_factory=list, description="Linear issue keys.")
    short_name: str = Field(default="", description="Short label (2-6 words).")
    category: str = Field(
        default=CommitCategory.OTHER,
        description="Primary section ID from the configured 'What changed' sections, or 'other'.",
    )
    consolidated_description: str = Field(default="", description="Human-readable description.")
    context: str = Field(
        default="", description="Context/motivation from issue tracker, empty string if none."
    )
    why: str = Field(default="", description="Reason/goal for the change, empty string if none.")
    upgrade_notes: list[str] = Field(default_factory=list, description="Upgrade-impact notes.")
    unknowns: list[str] = Field(default_factory=list, description="Ambiguities.")
    dependency_updates: list[DependencyUpdate] = Field(
        default_factory=list, description="Dependency updates for this commit."
    )


def build_consolidated_commit_model(config: ProjectConfig) -> type[ConsolidatedCommit]:
    """Build a ConsolidatedCommit model whose `category` enum matches configured sections."""
    category_ids = config.commit_category_ids()
    enum_members = {cid.upper(): cid for cid in category_ids}
    dynamic_enum = StrEnum("CommitCategoryDynamic", enum_members)
    default_val = (
        dynamic_enum("other") if "other" in category_ids else dynamic_enum(category_ids[0])
    )
    allowed_str = ", ".join(category_ids)
    return create_model(
        "ConsolidatedCommitDynamic",
        __base__=ConsolidatedCommit,
        category=(
            dynamic_enum,
            Field(
                default=default_val,
                description=f"Primary section ID from configured sections ({allowed_str}).",
            ),
        ),
    )


class ReleaseNoteItem(BaseModel):
    short_name: str = Field(description="Short label (2-6 words).")
    description: str = Field(description="1-4 sentence description.")
    evidence_paths: list[str] = Field(default_factory=list, description="Supporting file paths.")


class ReleaseNotesPayload(BaseModel):
    """Default payload used for dry-run and tests. Extra section ids are allowed."""

    model_config = ConfigDict(extra="allow")

    summary: list[ReleaseNoteItem] = Field(
        default_factory=list,
        description="3-6 high-signal, user-visible bullets.",
    )
    features: list[ReleaseNoteItem] = Field(default_factory=list)
    fixes: list[ReleaseNoteItem] = Field(default_factory=list)
    misc: list[ReleaseNoteItem] = Field(default_factory=list)
    ux_frontend: list[ReleaseNoteItem] = Field(default_factory=list)
    api_backend: list[ReleaseNoteItem] = Field(default_factory=list)
    python_api: list[ReleaseNoteItem] = Field(default_factory=list)
    infra_deps: list[ReleaseNoteItem] = Field(default_factory=list)
    upgrade_notes: list[ReleaseNoteItem] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


def payload_items(payload: BaseModel, section_id: str) -> list[ReleaseNoteItem]:
    """Read a list[ReleaseNoteItem] field from a (possibly dynamic) payload model."""
    val = getattr(payload, section_id, None)
    if not val:
        extra = getattr(payload, "model_extra", None) or {}
        val = extra.get(section_id)
    if not val:
        return []
    return list(val)


def set_payload_section(payload: BaseModel, section_id: str, items: list[ReleaseNoteItem]) -> None:
    setattr(payload, section_id, items)


def build_release_notes_payload_model(config: ProjectConfig) -> type[BaseModel]:
    """Pydantic model whose fields match enabled LLM-facing sections in config."""
    field_defs: dict[str, Any] = {}
    for sid in config.llm_section_ids():
        title = config.section_title(sid)
        field_defs[sid] = (
            list[ReleaseNoteItem],
            Field(default_factory=list, description=f"Items for section {title} ({sid})."),
        )
    field_defs["unknowns"] = (
        list[str],
        Field(default_factory=list, description="Ambiguities the model could not resolve."),
    )
    return create_model("ReleaseNotesPayloadDynamic", **field_defs)
