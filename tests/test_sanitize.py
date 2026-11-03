"""Tests for model-output sanitizers: Codex review bodies and generated release notes."""

from datetime import UTC, datetime

import codex_pr_assessment
import pytest

from release_notes.cli import _sanitize_llm_output
from release_notes.config import ProjectConfig
from release_notes.models import ConsolidatedCommit, ReleaseNoteItem, ReleaseNotesPayload, TagRange
from release_notes.renderer import render_markdown
from release_notes.sanitize import LINK_REMOVED, github_repo_link_filter, strip_untrusted_links

EVIL = "evil.example"


@pytest.mark.parametrize(
    ("body", "forbidden"),
    [
        pytest.param(f"See HTTPS://{EVIL}/x", EVIL, id="uppercase-scheme"),
        pytest.param(f"See HtTp://{EVIL}/x", EVIL, id="mixed-case-scheme"),
        pytest.param(f"[docs](//{EVIL}/x)", EVIL, id="protocol-relative-link"),
        pytest.param(f"Visit www.{EVIL}/x", EVIL, id="bare-www-autolink"),
        pytest.param(f"<https://{EVIL}/x>", EVIL, id="angle-bracket-autolink"),
        pytest.param("![x](https://github.com/o/r/a.png)", "![x]", id="image-on-allowed-host"),
        pytest.param(f"![x](https://{EVIL}/pixel.png)", EVIL, id="image-on-external-host"),
        pytest.param("![x][ref]", "![x]", id="reference-style-image"),
        pytest.param(f'<img src="https://{EVIL}/p.png">', "<img", id="raw-img-tag"),
        pytest.param('<a href="javascript:alert(1)">x</a>', "<a", id="raw-anchor-tag"),
        pytest.param("<!-- hidden instructions -->", "hidden", id="html-comment"),
        pytest.param(f"[x](&#104;ttps://{EVIL}/x)", EVIL, id="entity-encoded-scheme"),
        pytest.param(f"[x](&amp;#104;ttps://{EVIL}/x)", EVIL, id="double-entity-encoded-scheme"),
        pytest.param(f"[x](https\\://{EVIL}/x)", EVIL, id="backslash-escaped-scheme"),
        pytest.param(f"ht\u200btps://{EVIL}/x", EVIL, id="zero-width-space-in-scheme"),
        pytest.param("safe \u202etxt.exe", "\u202e", id="bidi-override"),
        pytest.param("a\u2066b\u2069c", "\u2066", id="bidi-isolate"),
    ],
)
def test_codex_sanitizer_blocks_link_and_markup_bypasses(body: str, forbidden: str) -> None:
    sanitized = codex_pr_assessment.strip_untrusted_links(body)

    assert forbidden not in sanitized


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("https://github.com/octo-org/example-app/pull/1", id="github-link"),
        pytest.param("HTTPS://GITHUB.COM/octo-org/example-app", id="uppercase-github-link"),
        pytest.param("https://sonarcloud.io/project/overview?id=x", id="sonarcloud-link"),
        pytest.param("`a < b` and `c > d`", id="comparison-operators"),
        pytest.param("// a code comment", id="double-slash-comment"),
    ],
)
def test_codex_sanitizer_keeps_allowed_content(body: str) -> None:
    assert codex_pr_assessment.strip_untrusted_links(body) == body


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        pytest.param("https://github.com/octo-org/example-app/pull/3", True, id="same-repo-pr"),
        pytest.param("https://github.com/Octo-Org/Example-App/issues/4", True, id="same-repo-case"),
        pytest.param(
            "https://github.com/octo-org/example-app-fork/pull/3", False, id="prefix-repo"
        ),
        pytest.param("https://github.com/someone/else/pull/3", False, id="other-github-repo"),
        pytest.param(f"https://{EVIL}/octo-org/example-app", False, id="external-host"),
    ],
)
def test_release_notes_filter_allows_only_this_repository(url: str, kept: bool) -> None:
    sanitized = strip_untrusted_links(
        f"See {url} now", github_repo_link_filter("octo-org/example-app")
    )

    assert (url in sanitized) is kept
    assert (LINK_REMOVED in sanitized) is not kept


def test_release_notes_filter_without_repository_removes_all_links() -> None:
    sanitized = strip_untrusted_links(
        "https://github.com/octo-org/example-app/pull/1", github_repo_link_filter(None)
    )

    assert sanitized == LINK_REMOVED


def test_release_notes_keep_tool_links_and_strip_llm_links() -> None:
    payload = ReleaseNotesPayload(
        summary=[
            ReleaseNoteItem(
                short_name="New API",
                description=f"Adds it. ![t](https://{EVIL}/t.png) See https://{EVIL}/x.",
            )
        ],
        unknowns=[f"Check <img src=https://{EVIL}/u.png>"],
    )
    consolidations = [
        ConsolidatedCommit(
            sha="a" * 40,
            subject="feat: new API",
            consolidated_description=f"Details at https://{EVIL}/d",
            upgrade_notes=[f"Read //{EVIL}/upgrade"],
        )
    ]

    consolidations = _sanitize_llm_output(payload, consolidations, "octo-org/example-app")
    markdown = render_markdown(
        payload,
        TagRange("v1.0.0", "v1.1.0"),
        version="1.1.0",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        compare_url="https://github.com/octo-org/example-app/compare/v1.0.0...v1.1.0",
        diff_stat="",
        commit_list=[],
        commit_digests=[],
        consolidations=consolidations,
        linear_issues_by_key={},
        github_issues_by_number={},
        include_digests_in_spoilers=False,
        dependency_bumps=[],
        file_diff_results={},
        config=ProjectConfig(),
    )

    assert EVIL not in markdown
    assert EVIL not in consolidations[0].consolidated_description
    assert EVIL not in consolidations[0].upgrade_notes[0]
    assert "https://github.com/octo-org/example-app/compare/v1.0.0...v1.1.0" in markdown


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(
            "azure_ai_api_key: ${{ secrets.AZURE_AI_API_KEY }}", id="action-input-secret-expression"
        ),
        pytest.param("GITHUB_TOKEN: ${{ github.token }}", id="env-token-expression"),
        pytest.param(
            "AZURE_AI_API_KEY=${{ secrets.AZURE_AI_API_KEY }}", id="assignment-expression"
        ),
    ],
)
def test_redaction_keeps_expression_references(line: str) -> None:
    assert codex_pr_assessment.redact_secret_references(line) == line


def test_redaction_still_redacts_literal_values_next_to_expressions() -> None:
    sanitized = codex_pr_assessment.redact_secret_references(
        "AZURE_AI_API_KEY: ${{ secrets.AZURE_AI_API_KEY }}\nGITHUB_TOKEN=literal-token-value"
    )

    assert "${{ secrets.AZURE_AI_API_KEY }}" in sanitized
    assert "literal-token-value" not in sanitized
