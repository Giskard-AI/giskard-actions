"""Tests for renderer.py — MarkdownWriter and render_markdown."""

from datetime import UTC, datetime

from release_notes.config import ProjectConfig
from release_notes.models import (
    ReleaseNoteItem,
    ReleaseNotesPayload,
    TagRange,
)
from release_notes.renderer import MarkdownWriter, render_markdown


class TestMarkdownWriter:
    def test_h1(self):
        w = MarkdownWriter()
        w.h1("Title")
        out = w.finish()
        assert "# Title" in out

    def test_h2(self):
        w = MarkdownWriter()
        w.h2("Section")
        out = w.finish()
        assert "## Section" in out

    def test_bullet(self):
        w = MarkdownWriter()
        w.bullet("item one")
        out = w.finish()
        assert "- item one" in out

    def test_blank_not_doubled(self):
        w = MarkdownWriter()
        w.h1("X")
        w.blank()
        w.blank()  # should not add a second blank
        lines = w.finish().splitlines()
        # Should not have two consecutive blanks from double blank() calls
        assert "  " not in "\n\n\n".join(lines)  # no triple newlines

    def test_details(self):
        w = MarkdownWriter()
        w.details("summary text", ["line 1", "line 2"])
        out = w.finish()
        assert "<details>" in out
        assert "<summary><code>summary text</code></summary>" in out
        assert "line 1" in out
        assert "</details>" in out

    def test_finish_ends_with_newline(self):
        w = MarkdownWriter()
        w.h1("X")
        assert w.finish().endswith("\n")


class TestRenderMarkdown:
    def _make_payload(self, **kwargs):
        defaults = dict(
            summary=[ReleaseNoteItem(short_name="S1", description="Summary one.")],
        )
        defaults.update(kwargs)
        return ReleaseNotesPayload(**defaults)

    def _render(self, payload=None, config=None, **kwargs):
        if payload is None:
            payload = self._make_payload()
        if config is None:
            config = ProjectConfig()
        defaults = dict(
            version="1.2.3",
            generated_at=datetime(2024, 1, 1, tzinfo=UTC),
            compare_url=None,
            diff_stat="",
            commit_list="",
            commit_digests=[],
            consolidations=[],
            linear_issues_by_key={},
            github_issues_by_number={},
            include_digests_in_spoilers=False,
            dependency_bumps=[],
            file_diff_results={},
            config=config,
        )
        defaults.update(kwargs)
        tag_range = TagRange(previous_tag="v1.1.0", current_tag="v1.2.3")
        return render_markdown(payload, tag_range, **defaults)

    def test_version_heading(self):
        out = self._render()
        assert "# Release notes v1.2.3" in out

    def test_summary_section_present(self):
        out = self._render()
        assert "Summary" in out
        assert "Summary one." in out

    def test_features_section_when_items(self):
        payload = self._make_payload(
            features=[ReleaseNoteItem(short_name="F1", description="A feature.")]
        )
        out = self._render(payload=payload)
        assert "What changed" in out
        assert "A feature." in out

    def test_misc_section_when_items(self):
        payload = self._make_payload(
            misc=[ReleaseNoteItem(short_name="M1", description="A misc thing.")]
        )
        out = self._render(payload=payload)
        assert "What changed" in out
        assert "A misc thing." in out

    def test_what_changed_absent_when_all_sections_disabled(self):
        # Disable all "what changed" sections; heading should not appear
        config = ProjectConfig()
        for sid in ("features", "fixes", "misc"):
            config = config.with_section_disabled(sid)
        out = self._render(config=config)
        assert "What changed" not in out

    def test_disabled_section_omitted(self):
        config = ProjectConfig().with_section_disabled("features")
        payload = self._make_payload(
            features=[ReleaseNoteItem(short_name="F1", description="A feature.")]
        )
        out = self._render(payload=payload, config=config)
        # features disabled → not rendered even if items present
        assert "A feature." not in out

    def test_range_in_output(self):
        out = self._render()
        assert "v1.1.0..v1.2.3" in out

    def test_file_diff_results_no_changes(self):
        out = self._render(file_diff_results={"helm/values.yaml": ""})
        assert "## `helm/values.yaml` diff" in out
        assert "_No changes in `helm/values.yaml`._" in out

    def test_file_diff_results_with_diff(self):
        diff_text = "-old: 1\n+new: 2\n"
        out = self._render(file_diff_results={"helm/values.yaml": diff_text})
        assert "## `helm/values.yaml` diff" in out
        assert "```diff" in out
        assert "-old: 1" in out
        assert "+new: 2" in out

    def test_custom_section_from_config(self):
        from release_notes.config import SectionConfig

        config = ProjectConfig(
            sections=(
                SectionConfig(id="summary", title="Summary", description=""),
                SectionConfig(id="python_api", title="Python API", description=""),
            )
        )
        payload = self._make_payload(
            python_api=[ReleaseNoteItem(short_name="Scan API", description="New scan.")]
        )
        out = self._render(payload=payload, config=config)
        assert "Python API" in out
        assert "New scan." in out
        assert "What changed" in out
