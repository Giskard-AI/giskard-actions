"""Project configuration loaded from .github/giskard/release-notes.json in the target repo."""

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

LOG = logging.getLogger("release_notes.config")

CONFIG_PATH = ".github/giskard/release-notes.json"

DETERMINISTIC_SECTION_IDS = frozenset({"deps", "appendix"})

# Base defaults — minimal set shared across all repos.
# Repos opt-in to additional sections (ux_frontend, api_backend, etc.) via release-notes.json.
DEFAULT_SECTIONS: list[dict] = [
    {
        "id": "summary",
        "title": "Summary",
        "description": (
            "3-6 high-signal, user-visible bullets summarizing the release. "
            "A subset of what changed, not a superset. "
            "For features and improvements, explicitly state the user benefit/outcome "
            "(e.g. 'This helps …', 'So you can …', 'This reduces …'); "
            "for bug fixes, state the resolved user-facing symptom directly."
        ),
        "enabled": True,
    },
    {
        "id": "features",
        "title": "Features",
        "description": (
            "New capabilities added in this release. "
            "Each item must explicitly state the user benefit/outcome "
            "(e.g. 'This helps …', 'So you can …', 'This reduces …')."
        ),
        "enabled": True,
    },
    {
        "id": "fixes",
        "title": "Fixes",
        "description": "Bug fixes.",
        "enabled": True,
    },
    {
        "id": "misc",
        "title": "Miscellaneous",
        "description": "Other notable changes that don't fit the sections above.",
        "enabled": True,
    },
]

# Extended section definitions used when repos opt-in via release-notes.json.
_KNOWN_SECTIONS: dict[str, dict] = {s["id"]: s for s in DEFAULT_SECTIONS} | {
    "ux_frontend": {
        "id": "ux_frontend",
        "title": "UX / Frontend",
        "description": "User interface and frontend changes.",
        "enabled": True,
    },
    "api_backend": {
        "id": "api_backend",
        "title": "API / Backend",
        "description": "API and backend changes.",
        "enabled": True,
    },
    "python_api": {
        "id": "python_api",
        "title": "Python API",
        "description": "Public Python SDK / library API changes.",
        "enabled": True,
    },
    "infra_deps": {
        "id": "infra_deps",
        "title": "Infra / Deps",
        "description": "Infrastructure, CI, and dependency changes.",
        "enabled": True,
    },
    "upgrade_notes": {
        "id": "upgrade_notes",
        "title": "Upgrade notes",
        "description": (
            "Operator actions required on upgrade (migrations, config, breaking API). "
            "Deterministic git rules may add more notes later; only add extra items here."
        ),
        "enabled": True,
    },
    "deps": {
        "id": "deps",
        "title": "Dependencies",
        "description": "Deterministic dependency version bump table.",
        "enabled": True,
    },
    "appendix": {
        "id": "appendix",
        "title": "Appendix",
        "description": "Diff stats, commit list, and per-commit details.",
        "enabled": True,
    },
}


@dataclass(frozen=True)
class SectionConfig:
    """Configuration for a single release notes section."""

    id: str
    title: str
    description: str
    enabled: bool = True


@dataclass(frozen=True)
class UpgradeRule:
    """Git-only rule that emits an upgrade note when matching paths changed."""

    id: str
    short_name: str
    description: str
    paths: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    filename_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    """Per-project configuration for release notes generation.

    Can be loaded from `.github/giskard/release-notes.json` in the target repo.
    """

    manifest_paths: tuple[str, ...] = ()
    high_signal_prefixes: tuple[str, ...] = ()
    ignored_basenames: frozenset[str] = frozenset({"uv.lock", "pnpm-lock.yaml"})
    github_repository: str | None = None
    file_diffs: tuple[str, ...] = ()
    ignored_path_prefixes: tuple[str, ...] = ()
    sections: tuple[SectionConfig, ...] = tuple(SectionConfig(**s) for s in DEFAULT_SECTIONS)
    upgrade_rules: tuple[UpgradeRule, ...] = ()
    model: str | None = None
    effort: str | None = None
    consolidation_model: str | None = None
    consolidation_effort: str | None = None
    synthesis_model: str | None = None
    synthesis_effort: str | None = None
    llm_concurrency: int | None = None

    def enabled_section_ids(self) -> set[str]:
        return {s.id for s in self.sections if s.enabled}

    def llm_section_ids(self) -> list[str]:
        """Enabled section ids that the LLM should fill (excludes deps/appendix)."""
        return [s.id for s in self.sections if s.enabled and s.id not in DETERMINISTIC_SECTION_IDS]

    def what_changed_section_ids(self) -> list[str]:
        skip = {"summary", "upgrade_notes"} | DETERMINISTIC_SECTION_IDS
        return [s.id for s in self.sections if s.enabled and s.id not in skip]

    def commit_category_ids(self) -> list[str]:
        """Allowed category IDs for Stage 2 commit consolidation."""
        ids = list(self.what_changed_section_ids())
        if "other" not in ids:
            ids.append("other")
        return ids

    def commit_category_descriptions_for_llm(self) -> str:
        """Build a text block describing Stage 2 commit categories for the LLM."""
        what_changed_ids = set(self.what_changed_section_ids())
        lines: list[str] = []
        for s in self.sections:
            if s.id in what_changed_ids:
                lines.append(f"- {s.id} ({s.title}): {s.description}")
        if "other" not in what_changed_ids:
            if what_changed_ids:
                lines.append(
                    "- other (Other): Purely internal changes (tests, CI, refactors, chores) "
                    "that do not belong in any section above."
                )
            else:
                lines.append("- other (Other): General changes in this commit.")
        return "\n".join(lines)

    def section_title(self, section_id: str) -> str:
        for s in self.sections:
            if s.id == section_id:
                return s.title
        return section_id

    def with_section_disabled(self, section_id: str) -> "ProjectConfig":
        """Return a new config with the given section disabled."""
        new_sections = tuple(
            SectionConfig(id=s.id, title=s.title, description=s.description, enabled=False)
            if s.id == section_id
            else s
            for s in self.sections
        )
        return replace(self, sections=new_sections)

    def section_descriptions_for_llm(self) -> str:
        """Build a text block describing enabled LLM-facing sections."""
        lines: list[str] = []
        for s in self.sections:
            if s.enabled and s.id not in DETERMINISTIC_SECTION_IDS:
                lines.append(f"- {s.id} ({s.title}): {s.description}")
        return "\n".join(lines)


def _parse_sections(raw: list[dict] | None) -> tuple[SectionConfig, ...]:
    """Parse the `sections` list from project config, overlaying onto known section metadata.

    When `raw` is non-empty, only the explicitly listed sections are included (in that exact order).
    When `raw` is None or empty, falls back to `DEFAULT_SECTIONS`.
    """
    if not raw:
        return tuple(SectionConfig(**s) for s in DEFAULT_SECTIONS)

    result: list[SectionConfig] = []
    for entry in raw:
        sid = (entry.get("id") or "").strip()
        if not sid:
            continue
        if not sid.isidentifier() or sid.startswith("_") or sid == "unknowns":
            raise SystemExit(
                f"Invalid section id '{sid}': must be a valid identifier and not reserved."
            )
        base = _KNOWN_SECTIONS.get(
            sid, {"id": sid, "title": sid, "description": "", "enabled": True}
        )
        merged = {**base, **entry}
        title = merged["title"] or sid
        description = merged["description"] or f"Changes related to {title}."
        result.append(
            SectionConfig(
                id=sid,
                title=title,
                description=description,
                enabled=merged.get("enabled", True),
            )
        )

    if not result:
        return tuple(SectionConfig(**s) for s in DEFAULT_SECTIONS)

    return tuple(result)


def _parse_upgrade_rules(raw: list[dict] | None) -> tuple[UpgradeRule, ...]:
    if not raw:
        return ()
    rules: list[UpgradeRule] = []
    for entry in raw:
        rid = (entry.get("id") or "").strip()
        short_name = (entry.get("short_name") or "").strip()
        description = (entry.get("description") or "").strip()
        if not rid or not short_name or not description:
            raise SystemExit("Each upgrade_rules entry needs id, short_name, and description.")
        rules.append(
            UpgradeRule(
                id=rid,
                short_name=short_name,
                description=description,
                paths=tuple(entry.get("paths") or ()),
                path_prefixes=tuple(entry.get("path_prefixes") or ()),
                filename_keywords=tuple(entry.get("filename_keywords") or ()),
            )
        )
    return tuple(rules)


def load_project_config(repo_root: Path) -> ProjectConfig:
    """Load project config from .github/giskard/release-notes.json, or use defaults."""
    config_path = repo_root / CONFIG_PATH
    if not config_path.exists():
        LOG.info("No %s found in %s, using defaults.", CONFIG_PATH, repo_root)
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
        file_diffs=tuple(data.get("file_diffs", [])),
        ignored_path_prefixes=tuple(data.get("ignored_path_prefixes", [])),
        sections=_parse_sections(data.get("sections")),
        upgrade_rules=_parse_upgrade_rules(data.get("upgrade_rules")),
        model=data.get("model"),
        effort=data.get("effort"),
        consolidation_model=data.get("consolidation_model"),
        consolidation_effort=data.get("consolidation_effort"),
        synthesis_model=data.get("synthesis_model"),
        synthesis_effort=data.get("synthesis_effort"),
        llm_concurrency=data.get("llm_concurrency"),
    )
