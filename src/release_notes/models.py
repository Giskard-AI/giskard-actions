"""Pydantic models and dataclasses used across the release notes pipeline."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field


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
    note_reason: str = Field(default="", description="Reason: security/breaking/compat/etc., empty string if none.")


class ConsolidatedCommit(BaseModel):
    sha: str = Field(default="", description="Full git SHA.")
    subject: str = Field(default="", description="Commit subject line.")
    pr_number: int = Field(default=0, description="PR number from subject like (#1234), 0 if none.")
    linear_keys: list[str] = Field(default_factory=list, description="Linear issue keys.")
    short_name: str = Field(default="", description="Short label (2-6 words).")
    category: CommitCategory = Field(default=CommitCategory.OTHER, description="Change category.")
    consolidated_description: str = Field(default="", description="Human-readable description.")
    context: str = Field(default="", description="Context/motivation from issue tracker, empty string if none.")
    why: str = Field(default="", description="Reason/goal for the change, empty string if none.")
    upgrade_notes: list[str] = Field(default_factory=list, description="Upgrade-impact notes.")
    unknowns: list[str] = Field(default_factory=list, description="Ambiguities.")
    dependency_updates: list[DependencyUpdate] = Field(
        default_factory=list, description="Dependency updates for this commit."
    )


class ReleaseNoteItem(BaseModel):
    short_name: str = Field(description="Short label (2-6 words).")
    description: str = Field(description="1-4 sentence description.")
    evidence_paths: list[str] = Field(default_factory=list, description="Supporting file paths.")


class ReleaseNotesPayload(BaseModel):
    summary: list[ReleaseNoteItem] = Field(
        description="3-6 high-signal, user-visible bullets."
    )
    features: list[ReleaseNoteItem] = Field(default_factory=list)
    fixes: list[ReleaseNoteItem] = Field(default_factory=list)
    ux_frontend: list[ReleaseNoteItem] = Field(default_factory=list)
    api_backend: list[ReleaseNoteItem] = Field(default_factory=list)
    infra_deps: list[ReleaseNoteItem] = Field(default_factory=list)
    upgrade_notes: list[ReleaseNoteItem] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
