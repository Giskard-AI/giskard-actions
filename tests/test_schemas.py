"""Published JSON Schemas agree with examples, this repo's consumer config, and the loaders."""

import json
from dataclasses import fields
from pathlib import Path

import codex_pr_assessment
import pytest
from jsonschema import Draft202012Validator

from release_notes.config import ProjectConfig, SectionConfig, UpgradeRule, load_project_config

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
SCHEMA_BASE_URL = "https://raw.githubusercontent.com/Giskard-AI/giskard-actions/main/schemas/"
CONFIG_FILES = sorted(
    [*(REPO_ROOT / "examples").glob("*.json"), *(REPO_ROOT / ".github/giskard").glob("*.json")]
)
SCHEMA_FILES = sorted(SCHEMAS_DIR.glob("*.schema.json"))


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _schema_name_for(path: Path) -> str:
    if path.name.endswith("release-notes.json"):
        return "release-notes.schema.json"
    if path.name == "codex-reviewer-map.json":
        return "codex-reviewer-map.schema.json"
    if path.name == "codex-ci-config.json":
        return "codex-ci-config.schema.json"
    raise AssertionError(f"no schema mapped for {_rel(path)}")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(_load_json(SCHEMAS_DIR / schema_name))


def test_config_files_are_discovered():
    assert {path.parent.name for path in CONFIG_FILES} == {"examples", "giskard"}


@pytest.mark.parametrize("schema_path", SCHEMA_FILES, ids=[p.name for p in SCHEMA_FILES])
def test_schema_is_valid_draft_2020_12_with_published_id(schema_path):
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == SCHEMA_BASE_URL + schema_path.name


@pytest.mark.parametrize("config_path", CONFIG_FILES, ids=[_rel(p) for p in CONFIG_FILES])
def test_config_file_matches_its_schema(config_path):
    schema_name = _schema_name_for(config_path)
    data = _load_json(config_path)
    errors = sorted(_validator(schema_name).iter_errors(data), key=lambda e: e.json_path)
    assert not errors, "\n".join(f"{e.json_path}: {e.message}" for e in errors)
    assert data["$schema"] == SCHEMA_BASE_URL + schema_name


@pytest.mark.parametrize("config_path", CONFIG_FILES, ids=[_rel(p) for p in CONFIG_FILES])
def test_config_file_loads_with_runtime_loader(config_path, tmp_path, write_release_notes_config):
    schema_name = _schema_name_for(config_path)
    if schema_name == "release-notes.schema.json":
        write_release_notes_config(config_path.read_text(encoding="utf-8"))
        assert load_project_config(tmp_path).sections
    elif schema_name == "codex-reviewer-map.schema.json":
        rules, _ = codex_pr_assessment.load_reviewer_map_config(config_path)
        assert rules
    else:
        assert codex_pr_assessment.load_ci_config(config_path)["key_workflows"]


@pytest.mark.parametrize(
    ("schema_properties", "dataclass_type"),
    [
        (lambda s: s["properties"], ProjectConfig),
        (lambda s: s["$defs"]["section"]["properties"], SectionConfig),
        (lambda s: s["$defs"]["upgradeRule"]["properties"], UpgradeRule),
    ],
    ids=[
        "top_level_matches_ProjectConfig",
        "section_matches_SectionConfig",
        "rule_matches_UpgradeRule",
    ],
)
def test_release_notes_schema_tracks_config_dataclasses(schema_properties, dataclass_type):
    schema = _load_json(SCHEMAS_DIR / "release-notes.schema.json")
    properties = set(schema_properties(schema)) - {"$schema"}
    assert properties == {field.name for field in fields(dataclass_type)}


@pytest.mark.parametrize(
    ("schema_name", "document"),
    [
        ("release-notes.schema.json", {"manifest_path": ["pyproject.toml"]}),
        ("release-notes.schema.json", {"sections": [{"id": "unknowns"}]}),
        ("release-notes.schema.json", {"sections": [{"id": "ux-frontend"}]}),
        ("release-notes.schema.json", {"upgrade_rules": [{"id": "x", "short_name": "X"}]}),
        ("release-notes.schema.json", {"trusted_github_authors": [{"login": "octocat"}]}),
        ("codex-reviewer-map.schema.json", {"rules": []}),
        ("codex-reviewer-map.schema.json", {"rules": [{"name": "bad name"}]}),
        (
            "codex-reviewer-map.schema.json",
            {"rules": [{"name": "x", "role_references": ["../secret.md"]}]},
        ),
        ("codex-reviewer-map.schema.json", {"rules": [{"name": "x", "model_kind": "urgent"}]}),
        ("codex-ci-config.schema.json", {"key_workflows": "CI"}),
        ("codex-ci-config.schema.json", {"recommendations": [{"action": "apply_label"}]}),
    ],
    ids=[
        "release_notes_misspelled_top_level_key",
        "release_notes_reserved_section_id",
        "release_notes_hyphenated_section_id",
        "release_notes_upgrade_rule_missing_description",
        "release_notes_trusted_author_missing_id",
        "reviewer_map_empty_rules",
        "reviewer_map_invalid_reviewer_name",
        "reviewer_map_role_reference_path_traversal",
        "reviewer_map_unknown_model_kind",
        "ci_config_key_workflows_not_array",
        "ci_config_recommendation_missing_target",
    ],
)
def test_schema_rejects_invalid_document(schema_name, document):
    assert not _validator(schema_name).is_valid(document)
