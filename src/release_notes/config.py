"""Project-specific configuration loaded from .release-notes.json in the target repo."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("release_notes.config")

CONFIG_FILENAME = ".release-notes.json"


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
    )
