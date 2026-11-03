"""Tests for config.py — defaults, overlay logic, file loading."""

import json
from pathlib import Path

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

    def test_explicit_sections_are_authoritative_without_appending_defaults(self):
        raw = [{"id": "features"}]
        result = _parse_sections(raw)
        ids = [s.id for s in result]
        assert ids == ["features"]

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

    def test_reads_sections_from_file(self, tmp_path, write_release_notes_config):
        data = {
            "sections": [
                {"id": "summary"},
                {"id": "features"},
                {"id": "fixes"},
                {"id": "ux_frontend"},
            ]
        }
        write_release_notes_config(json.dumps(data))
        cfg = load_project_config(tmp_path)
        ids = [s.id for s in cfg.sections]
        assert "ux_frontend" in ids

    def test_reads_manifest_paths(self, tmp_path, write_release_notes_config):
        data = {"manifest_paths": ["pyproject.toml", "frontend/package.json"]}
        write_release_notes_config(json.dumps(data))
        cfg = load_project_config(tmp_path)
        assert "pyproject.toml" in cfg.manifest_paths

    def test_reads_file_diffs(self, tmp_path, write_release_notes_config):
        data = {"file_diffs": ["deploy/helm/example-web-app/values.yaml"]}
        write_release_notes_config(json.dumps(data))
        cfg = load_project_config(tmp_path)
        assert "deploy/helm/example-web-app/values.yaml" in cfg.file_diffs

    def test_reads_upgrade_rules(self, tmp_path, write_release_notes_config):
        data = {
            "upgrade_rules": [
                {
                    "id": "helm_values",
                    "short_name": "Helm values",
                    "description": "Review values.",
                    "paths": ["helm/values.yaml"],
                }
            ]
        }
        write_release_notes_config(json.dumps(data))
        cfg = load_project_config(tmp_path)
        assert len(cfg.upgrade_rules) == 1
        assert cfg.upgrade_rules[0].id == "helm_values"
        assert cfg.upgrade_rules[0].paths == ("helm/values.yaml",)

    @pytest.mark.parametrize(
        "example",
        [
            "web-app.release-notes.json",
            "python-library.release-notes.json",
            "minimal.release-notes.json",
        ],
        ids=["web-app", "python-library", "minimal"],
    )
    def test_example_json_files_load(self, tmp_path, write_release_notes_config, example):
        src = Path(__file__).resolve().parents[1] / "examples" / example
        write_release_notes_config(src.read_text(encoding="utf-8"))
        cfg = load_project_config(tmp_path)
        assert cfg.sections
        if example == "web-app.release-notes.json":
            assert cfg.upgrade_rules
            assert cfg.file_diffs
        if example == "python-library.release-notes.json":
            assert any(s.id == "python_api" for s in cfg.sections)
            assert not any(s.id == "ux_frontend" for s in cfg.sections if s.enabled)

    def test_legacy_root_config_is_ignored(self, tmp_path):
        (tmp_path / ".release-notes.json").write_text(json.dumps({"model": "legacy"}))
        assert load_project_config(tmp_path).model is None


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

    def test_preserves_upgrade_rules(self):
        from release_notes.config import UpgradeRule

        cfg = ProjectConfig(
            upgrade_rules=(UpgradeRule(id="x", short_name="X", description="d", paths=("a",)),)
        )
        new_cfg = cfg.with_section_disabled("fixes")
        assert new_cfg.upgrade_rules == cfg.upgrade_rules


class TestCommitCategoryHelpers:
    @pytest.mark.parametrize(
        ("sections_raw", "expected_ids"),
        [
            (
                None,
                ["features", "fixes", "misc", "other"],
            ),
            (
                [
                    {"id": "summary"},
                    {"id": "features"},
                    {"id": "fixes"},
                    {"id": "ux_frontend"},
                    {"id": "api_backend"},
                    {"id": "infra_deps"},
                    {"id": "upgrade_notes"},
                    {"id": "deps"},
                    {"id": "appendix"},
                ],
                ["features", "fixes", "ux_frontend", "api_backend", "infra_deps", "other"],
            ),
            (
                [
                    {"id": "summary"},
                    {"id": "features"},
                    {"id": "probes", "title": "Probes", "description": "Adversarial probes."},
                    {"id": "python_api"},
                    {"id": "fixes", "enabled": False},
                    {"id": "deps"},
                ],
                ["features", "probes", "python_api", "other"],
            ),
        ],
        ids=["default_config", "web_app_sections", "custom_probes_and_disabled_fixes"],
    )
    def test_commit_category_ids(self, sections_raw, expected_ids):
        cfg = ProjectConfig(sections=_parse_sections(sections_raw))
        assert cfg.commit_category_ids() == expected_ids

    def test_commit_category_descriptions_for_llm(self):
        raw = [
            {"id": "summary"},
            {"id": "probes", "title": "Probes", "description": "Adversarial probes."},
            {"id": "python_api"},
            {"id": "upgrade_notes"},
            {"id": "deps"},
        ]
        cfg = ProjectConfig(sections=_parse_sections(raw))
        desc = cfg.commit_category_descriptions_for_llm()
        assert "- probes (Probes): Adversarial probes." in desc
        assert "- python_api (Python API):" in desc
        assert "- other (Other):" in desc
        assert "- summary" not in desc
        assert "- upgrade_notes" not in desc
        assert "- deps" not in desc

    @pytest.mark.parametrize(
        "invalid_id",
        ["ux-frontend", "_private", "123", "unknowns"],
        ids=["hyphenated", "leading_underscore", "numeric", "reserved_unknowns"],
    )
    def test_invalid_section_id_raises(self, invalid_id):
        with pytest.raises(SystemExit, match="Invalid section id"):
            _parse_sections([{"id": invalid_id}])

    def test_custom_section_default_description(self):
        sections = _parse_sections([{"id": "probes", "title": "Probes"}])
        assert sections[0].description == "Changes related to Probes."

    def test_default_llm_model_and_effort(self):
        from giskard.llm.translators import openai_chat
        from giskard.llm.types import UserMessage

        from release_notes.llm import (
            DEFAULT_EFFORT,
            DEFAULT_MODEL,
            _ensure_reasoning_effort_support,
        )

        assert DEFAULT_MODEL == "azure_ai/gpt-5.6-luna"
        assert DEFAULT_EFFORT == "high"
        _ensure_reasoning_effort_support()
        translated = openai_chat.OpenAIChatTranslator.to_openai(
            "gpt-5.6-luna",
            [UserMessage(role="user", content="hello")],
            reasoning_effort="high",
        )
        assert translated["reasoning_effort"] == "high"

    def test_repo_release_notes_config_is_valid(self):
        repo_root = Path(__file__).resolve().parents[1]
        cfg = load_project_config(repo_root)
        assert cfg.github_repository == "Giskard-AI/giskard-actions"
        assert cfg.model == "azure_ai/gpt-5.6-luna"
        assert cfg.effort == "high"
        assert cfg.manifest_paths == ("pyproject.toml",)
        assert ".github/actions/release-notes/action.yml" in cfg.file_diffs
        assert ".github/actions/codex-pr-review/action.yml" in cfg.file_diffs
        assert len(cfg.upgrade_rules) == 1
        assert cfg.upgrade_rules[0].id == "action_interface"
        assert [s.id for s in cfg.sections] == [
            "summary",
            "features",
            "fixes",
            "infra_deps",
            "upgrade_notes",
            "deps",
            "appendix",
        ]
