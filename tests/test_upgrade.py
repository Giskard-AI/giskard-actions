"""Tests for deterministic upgrade notes."""

from release_notes.config import UpgradeRule
from release_notes.models import ReleaseNoteItem
from release_notes.upgrade import apply_upgrade_rules, merge_upgrade_notes

HELM = UpgradeRule(
    id="helm_values",
    short_name="Helm values",
    description="Review values.yaml.",
    paths=("helm/giskard-llm-hub/values.yaml",),
)
MIGRATIONS = UpgradeRule(
    id="migrations",
    short_name="Database migrations",
    description="Take a DB backup.",
    path_prefixes=("backend/migrations/versions/",),
)
DOCS = UpgradeRule(
    id="deploy_docs",
    short_name="Deployment docs",
    description="Review deploy docs.",
    paths=(".github/workflows/test-local-package.yml",),
    path_prefixes=("docs/",),
    filename_keywords=("helm", "deploy"),
)


class TestApplyUpgradeRules:
    def test_exact_path_match(self):
        notes = apply_upgrade_rules((HELM,), ["helm/giskard-llm-hub/values.yaml", "README.md"])
        assert len(notes) == 1
        assert notes[0].short_name == "Helm values"
        assert notes[0].evidence_paths == ["helm/giskard-llm-hub/values.yaml"]

    def test_prefix_match(self):
        notes = apply_upgrade_rules((MIGRATIONS,), ["backend/migrations/versions/abc.py"])
        assert len(notes) == 1
        assert notes[0].short_name == "Database migrations"

    def test_no_match_skips_rule(self):
        notes = apply_upgrade_rules((HELM, MIGRATIONS), ["frontend/src/app/page.tsx"])
        assert notes == []

    def test_filename_keywords_filter_docs(self):
        notes = apply_upgrade_rules((DOCS,), ["docs/architecture.md", "docs/helm-install.md"])
        assert len(notes) == 1
        assert notes[0].evidence_paths == ["docs/helm-install.md"]

    def test_docs_workflow_path(self):
        notes = apply_upgrade_rules((DOCS,), [".github/workflows/test-local-package.yml"])
        assert len(notes) == 1

    def test_order_follows_rules(self):
        notes = apply_upgrade_rules(
            (HELM, MIGRATIONS),
            [
                "backend/migrations/versions/x.py",
                "helm/giskard-llm-hub/values.yaml",
            ],
        )
        assert [n.short_name for n in notes] == ["Helm values", "Database migrations"]


class TestMergeUpgradeNotes:
    def test_appends_new_llm_short_names(self):
        det = [ReleaseNoteItem(short_name="Helm values", description="d")]
        llm = [
            ReleaseNoteItem(short_name="Helm values", description="duplicate"),
            ReleaseNoteItem(short_name="Breaking API", description="extra"),
        ]
        merged = merge_upgrade_notes(det, llm)
        assert [m.short_name for m in merged] == ["Helm values", "Breaking API"]
        assert merged[0].description == "d"

    def test_empty_llm(self):
        det = [ReleaseNoteItem(short_name="Helm values", description="d")]
        assert merge_upgrade_notes(det, []) == det
