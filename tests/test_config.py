"""Tests for config.py — defaults, overlay logic, file loading."""

import json

import pytest

from release_notes.config import (
    DEFAULT_SECTIONS,
    ProjectConfig,
    _parse_sections,
    load_project_config,
)


class TestDefaultSections:
    def test_exactly_four_base_sections(self):
        assert len(DEFAULT_SECTIONS) == 4

    def test_expected_ids(self):
        ids = {s["id"] for s in DEFAULT_SECTIONS}
        assert ids == {"summary", "features", "fixes", "misc"}

    def test_all_enabled(self):
        assert all(s["enabled"] for s in DEFAULT_SECTIONS)


class TestProjectConfigDefaults:
    def test_four_sections(self):
        cfg = ProjectConfig()
        assert len(cfg.sections) == 4

    def test_default_section_ids(self):
        cfg = ProjectConfig()
        ids = {s.id for s in cfg.sections}
        assert ids == {"summary", "features", "fixes", "misc"}

    def test_all_enabled_by_default(self):
        cfg = ProjectConfig()
        assert all(s.enabled for s in cfg.sections)


class TestParseSections:
    def test_empty_list_uses_defaults(self):
        result = _parse_sections([])
        ids = {s.id for s in result}
        assert ids == {"summary", "features", "fixes", "misc"}

    def test_none_uses_defaults(self):
        result = _parse_sections(None)
        ids = {s.id for s in result}
        assert ids == {"summary", "features", "fixes", "misc"}

    def test_user_listed_section_merged(self):
        raw = [{"id": "features", "title": "New Features", "enabled": True}]
        result = _parse_sections(raw)
        section = next(s for s in result if s.id == "features")
        assert section.title == "New Features"

    def test_user_order_preserved(self):
        raw = [
            {"id": "fixes"},
            {"id": "features"},
            {"id": "summary"},
        ]
        result = _parse_sections(raw)
        ids = [s.id for s in result[:3]]
        assert ids == ["fixes", "features", "summary"]

    def test_defaults_appended_for_missing(self):
        raw = [{"id": "features"}]
        result = _parse_sections(raw)
        ids = [s.id for s in result]
        # features is first (user listed), the other defaults are appended
        assert ids[0] == "features"
        assert set(ids) >= {"features", "summary", "fixes", "misc"}

    def test_known_extended_section_gets_metadata(self):
        # ux_frontend is not a default but is a known section
        raw = [{"id": "ux_frontend"}]
        result = _parse_sections(raw)
        section = next(s for s in result if s.id == "ux_frontend")
        assert section.title == "UX / Frontend"

    def test_unknown_section_gets_fallback_metadata(self):
        raw = [{"id": "custom_thing"}]
        result = _parse_sections(raw)
        section = next(s for s in result if s.id == "custom_thing")
        assert section.title == "custom_thing"
        assert section.enabled is True

    def test_disabled_section(self):
        raw = [{"id": "fixes", "enabled": False}]
        result = _parse_sections(raw)
        section = next(s for s in result if s.id == "fixes")
        assert section.enabled is False


class TestLoadProjectConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_project_config(tmp_path)
        assert len(cfg.sections) == 4

    def test_reads_sections_from_file(self, tmp_path):
        data = {
            "sections": [
                {"id": "summary"},
                {"id": "features"},
                {"id": "fixes"},
                {"id": "ux_frontend"},
            ]
        }
        (tmp_path / ".release-notes.json").write_text(json.dumps(data))
        cfg = load_project_config(tmp_path)
        ids = [s.id for s in cfg.sections]
        assert "ux_frontend" in ids

    def test_reads_manifest_paths(self, tmp_path):
        data = {"manifest_paths": ["pyproject.toml", "frontend/package.json"]}
        (tmp_path / ".release-notes.json").write_text(json.dumps(data))
        cfg = load_project_config(tmp_path)
        assert "pyproject.toml" in cfg.manifest_paths

    def test_reads_file_diffs(self, tmp_path):
        data = {"file_diffs": ["helm/giskard-llm-hub/values.yaml"]}
        (tmp_path / ".release-notes.json").write_text(json.dumps(data))
        cfg = load_project_config(tmp_path)
        assert "helm/giskard-llm-hub/values.yaml" in cfg.file_diffs

    def test_file_diffs_defaults_empty(self, tmp_path):
        (tmp_path / ".release-notes.json").write_text("{}")
        cfg = load_project_config(tmp_path)
        assert cfg.file_diffs == ()


class TestWithSectionDisabled:
    def test_disables_one_section(self):
        cfg = ProjectConfig()
        new_cfg = cfg.with_section_disabled("fixes")
        section = next(s for s in new_cfg.sections if s.id == "fixes")
        assert section.enabled is False

    def test_other_sections_unchanged(self):
        cfg = ProjectConfig()
        new_cfg = cfg.with_section_disabled("fixes")
        others = [s for s in new_cfg.sections if s.id != "fixes"]
        assert all(s.enabled for s in others)

    def test_nonexistent_section_is_noop(self):
        cfg = ProjectConfig()
        new_cfg = cfg.with_section_disabled("nonexistent")
        assert len(new_cfg.sections) == len(cfg.sections)
