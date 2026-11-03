"""Tests for dynamic payload models."""

import pytest
from pydantic import ValidationError

from release_notes.config import ProjectConfig, _parse_sections, load_project_config
from release_notes.models import (
    ReleaseNoteItem,
    build_consolidated_commit_model,
    build_release_notes_payload_model,
    payload_items,
    set_payload_section,
)


class TestBuildPayloadModel:
    def test_default_includes_summary_not_deps(self):
        cfg = ProjectConfig()
        model = build_release_notes_payload_model(cfg)
        fields = set(model.model_fields)
        assert "summary" in fields
        assert "features" in fields
        assert "unknowns" in fields
        assert "deps" not in fields
        assert "appendix" not in fields

    def test_python_library_sections_without_ux(self, tmp_path, write_release_notes_config):
        write_release_notes_config(
            """
            {
              "sections": [
                {"id": "summary"},
                {"id": "features"},
                {"id": "fixes"},
                {"id": "python_api"},
                {"id": "upgrade_notes"},
                {"id": "deps"},
                {"id": "appendix"}
              ]
            }
            """
        )
        cfg = load_project_config(tmp_path)
        model = build_release_notes_payload_model(cfg)
        fields = set(model.model_fields)
        assert "python_api" in fields
        assert "ux_frontend" not in fields
        instance = model(python_api=[ReleaseNoteItem(short_name="API", description="Changed.")])
        assert payload_items(instance, "python_api")[0].short_name == "API"


class TestPayloadItems:
    def test_set_and_get(self):
        from release_notes.models import ReleaseNotesPayload

        payload = ReleaseNotesPayload()
        items = [ReleaseNoteItem(short_name="A", description="B")]
        set_payload_section(payload, "upgrade_notes", items)
        assert payload_items(payload, "upgrade_notes") == items


class TestBuildConsolidatedCommitModel:
    @pytest.mark.parametrize(
        ("sections_raw", "valid_category", "invalid_category"),
        [
            (
                None,
                "features",
                "ux_frontend",
            ),
            (
                [
                    {"id": "summary"},
                    {"id": "features"},
                    {"id": "ux_frontend"},
                    {"id": "api_backend"},
                ],
                "ux_frontend",
                "python_api",
            ),
            (
                [
                    {"id": "summary"},
                    {"id": "probes", "title": "Probes", "description": "Adversarial probes."},
                    {"id": "python_api"},
                ],
                "probes",
                "ux_frontend",
            ),
        ],
        ids=["default_sections", "web_app_sections", "custom_probes_section"],
    )
    def test_dynamic_category_validation(self, sections_raw, valid_category, invalid_category):
        cfg = ProjectConfig(sections=_parse_sections(sections_raw))
        model = build_consolidated_commit_model(cfg)

        instance = model(short_name="Test", category=valid_category)
        assert instance.category == valid_category

        other_instance = model(short_name="Chore", category="other")
        assert other_instance.category == "other"

        with pytest.raises(ValidationError):
            model(short_name="Invalid", category=invalid_category)

    def test_llm_schema_excludes_canonical_git_fields_and_evidence_paths(self):
        cfg = ProjectConfig()
        commit_model = build_consolidated_commit_model(cfg)
        assert "sha" not in commit_model.model_fields
        assert "subject" not in commit_model.model_fields
        assert "pr_number" not in commit_model.model_fields
        assert "linear_keys" not in commit_model.model_fields

        payload_model = build_release_notes_payload_model(cfg)
        schema_str = str(payload_model.model_json_schema())
        assert "evidence_paths" not in schema_str
