"""Project-specific configuration loaded from .release-notes.json in the target repo."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("release_notes.config")

CONFIG_FILENAME = ".release-notes.json"

# All known section types and their defaults
DEFAULT_SECTIONS: list[dict] = [
    {
        "id": "summary",
        "title": "Summary",
        "description": "3-6 high-signal, user-visible bullets summarizing the release.",
        "enabled": True,
    },
    {
        "id": "features",
        "title": "Features",
        "description": "New capabilities added in this release.",
        "enabled": True,
    },
    {
        "id": "fixes",
        "title": "Fixes",
        "description": "Bug fixes.",
        "enabled": True,
    },
    {
        "id": "ux_frontend",
        "title": "UX / Frontend",
        "description": "User interface and frontend changes.",
        "enabled": True,
    },
    {
        "id": "api_backend",
        "title": "API / Backend",
        "description": "API and backend changes.",
        "enabled": True,
    },
    {
        "id": "infra_deps",
        "title": "Infra / Deps",
        "description": "Infrastructure, CI, and dependency changes.",
        "enabled": True,
    },
    {
        "id": "upgrade_notes",
        "title": "Upgrade notes",
        "description": "Migrations, config changes, breaking changes.",
        "enabled": True,
    },
    {
        "id": "deps",
        "title": "Dependencies",
        "description": "Deterministic dependency version bump table.",
        "enabled": True,
    },
    {
        "id": "appendix",
        "title": "Appendix",
        "description": "Diff stats, commit list, and per-commit details.",
        "enabled": True,
    },
]


@dataclass(frozen=True)
class SectionConfig:
    """Configuration for a single release notes section."""

    id: str
    title: str
    description: str
    enabled: bool = True


@dataclass(frozen=True)
class ProjectConfig:
    """Per-project configuration for release notes generation.

    Can be loaded from a `.release-notes.json` file at the root of the target repo.
    """

    # Paths to dependency manifest files (pyproject.toml, package.json, pom.xml)
    manifest_paths: tuple[str, ...] = ()

    # Path prefixes considered "high-signal" for diff extraction (ordered by priority)
    high_signal_prefixes: tuple[str, ...] = ()

    # File basenames to ignore in diffs (e.g. lock files)
    ignored_basenames: frozenset[str] = frozenset({"uv.lock", "pnpm-lock.yaml"})

    # GitHub repository slug (owner/repo) for compare URLs
    github_repository: str | None = None

    # Sections configuration
    sections: tuple[SectionConfig, ...] = tuple(
        SectionConfig(**s) for s in DEFAULT_SECTIONS
    )

    def enabled_section_ids(self) -> set[str]:
        return {s.id for s in self.sections if s.enabled}

    def section_title(self, section_id: str) -> str:
        for s in self.sections:
            if s.id == section_id:
                return s.title
        return section_id

    def with_section_disabled(self, section_id: str) -> "ProjectConfig":
        """Return a new config with the given section disabled."""
        new_sections = tuple(
            SectionConfig(id=s.id, title=s.title, description=s.description, enabled=False)
            if s.id == section_id else s
            for s in self.sections
        )
        return ProjectConfig(
            manifest_paths=self.manifest_paths,
            high_signal_prefixes=self.high_signal_prefixes,
            ignored_basenames=self.ignored_basenames,
            github_repository=self.github_repository,
            sections=new_sections,
        )

    def section_descriptions_for_llm(self) -> str:
        """Build a text block describing enabled sections, for LLM prompt context."""
        lines: list[str] = []
        for s in self.sections:
            if s.enabled and s.id not in ("deps", "appendix"):
                lines.append(f"- {s.id} ({s.title}): {s.description}")
        return "\n".join(lines)


def _parse_sections(raw: list[dict] | None) -> tuple[SectionConfig, ...]:
    if not raw:
        return tuple(SectionConfig(**s) for s in DEFAULT_SECTIONS)

    # Start from defaults, then overlay user config
    defaults_by_id = {s["id"]: dict(s) for s in DEFAULT_SECTIONS}
    result: list[SectionConfig] = []

    # Track which sections user explicitly listed (preserves their order)
    seen_ids: set[str] = set()
    for entry in raw:
        sid = entry.get("id", "")
        if not sid:
            continue
        seen_ids.add(sid)
        base = defaults_by_id.get(sid, {"id": sid, "title": sid, "description": "", "enabled": True})
        merged = {**base, **entry}
        result.append(SectionConfig(**merged))

    # Append any default sections not mentioned by user (preserving default order)
    for s in DEFAULT_SECTIONS:
        if s["id"] not in seen_ids:
            result.append(SectionConfig(**s))

    return tuple(result)


def load_project_config(repo_root: Path) -> ProjectConfig:
    """Load project config from .release-notes.json in the target repo, or use defaults."""
    config_path = repo_root / CONFIG_FILENAME
    if not config_path.exists():
        LOG.info("No %s found in %s, using defaults.", CONFIG_FILENAME, repo_root)
        return ProjectConfig()

    LOG.info("Loading project config from %s", config_path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"Failed to parse {config_path}: {e}") from e

    return ProjectConfig(
        manifest_paths=tuple(data.get("manifest_paths", [])),
        high_signal_prefixes=tuple(data.get("high_signal_prefixes", [])),
        ignored_basenames=frozenset(data.get("ignored_basenames", ["uv.lock", "pnpm-lock.yaml"])),
        github_repository=data.get("github_repository"),
        sections=_parse_sections(data.get("sections")),
    )
