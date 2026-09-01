"""Tests for dynamic payload models."""

from release_notes.config import ProjectConfig, load_project_config
from release_notes.models import (
    ReleaseNoteItem,
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

    def test_lidar_style_python_api_without_ux(self, tmp_path):
        (tmp_path / ".release-notes.json").write_text(
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
