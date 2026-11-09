"""Tests for model-output sanitizers: Codex review bodies and generated release notes."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import codex_pr_assessment
import pytest

from release_notes.cli import _sanitize_llm_output
from release_notes.config import ProjectConfig
from release_notes.models import ConsolidatedCommit, ReleaseNoteItem, ReleaseNotesPayload, TagRange
from release_notes.renderer import render_markdown
from release_notes.sanitize import LINK_REMOVED, github_repo_link_filter, strip_untrusted_links

EVIL = "evil.example"
REPO = "Giskard-AI/repo-baseline"
SHA = "a22733cfae5a41cbff59cc9af0a13c367797543b"
BLOB = f"https://github.com/{REPO}/blob/{SHA}"
RUNNER_WORKSPACE = "/home/runner/work/repo-baseline/repo-baseline"


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
    ("body", "expected"),
    [
        pytest.param(
            f"[.github/workflows/dashboard.yml:19]({RUNNER_WORKSPACE}/.github/workflows/dashboard.yml:19)",
            f"[.github/workflows/dashboard.yml:19]({BLOB}/.github/workflows/dashboard.yml#L19)",
            id="pr11-runner-workspace-link-with-colon-line",
        ),
        pytest.param(
            f"[a.py]({RUNNER_WORKSPACE}/src/a.py#L7)",
            f"[a.py]({BLOB}/src/a.py#L7)",
            id="runner-workspace-link-with-line-anchor",
        ),
        pytest.param(
            f"[a.py]({RUNNER_WORKSPACE}/src/a.py:7-12)",
            f"[a.py]({BLOB}/src/a.py#L7-L12)",
            id="runner-workspace-link-with-line-range",
        ),
        pytest.param(
            f"[a.py]({RUNNER_WORKSPACE}/src/a.py:7:3)",
            f"[a.py]({BLOB}/src/a.py#L7)",
            id="runner-workspace-link-with-line-and-column",
        ),
        pytest.param(
            f"[README.md]({RUNNER_WORKSPACE}/README.md)",
            f"[README.md]({BLOB}/README.md)",
            id="runner-workspace-link-without-line",
        ),
        pytest.param(
            f"[a.py](file://{RUNNER_WORKSPACE}/src/a.py:7)",
            f"[a.py]({BLOB}/src/a.py#L7)",
            id="file-scheme-runner-workspace-link",
        ),
        pytest.param(
            "[a.py](/__w/repo-baseline/repo-baseline/src/a.py:7)",
            f"[a.py]({BLOB}/src/a.py#L7)",
            id="container-job-workspace-link",
        ),
        pytest.param(
            "[a.py](/srv/actions/_work/repo-baseline/repo-baseline/src/a.py:7)",
            f"[a.py]({BLOB}/src/a.py#L7)",
            id="github-workspace-env-link",
        ),
        pytest.param(
            "[passwd:1](/etc/passwd:1)",
            "`passwd:1`",
            id="unknown-absolute-path-link-becomes-inline-code",
        ),
        pytest.param(
            f"[{RUNNER_WORKSPACE}/src/a.py:7]({RUNNER_WORKSPACE}/src/a.py:7)",
            f"[src/a.py:7]({BLOB}/src/a.py#L7)",
            id="absolute-label-made-relative",
        ),
        pytest.param(
            "[a.py](src/a.py)",
            "[a.py](src/a.py)",
            id="relative-link-unchanged",
        ),
        pytest.param(
            f"[run]({BLOB}/src/a.py#L7)",
            f"[run]({BLOB}/src/a.py#L7)",
            id="github-blob-link-unchanged",
        ),
    ],
)
def test_review_rewrites_local_path_links_to_blob_urls(
    monkeypatch: pytest.MonkeyPatch, body: str, expected: str
) -> None:
    monkeypatch.setenv("GITHUB_WORKSPACE", "/srv/actions/_work/repo-baseline/repo-baseline")

    sanitized = codex_pr_assessment.sanitize_review_body(
        body, "Codex advisory code review", repository=REPO, head_sha=SHA
    )

    assert expected in sanitized
    assert RUNNER_WORKSPACE not in sanitized


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            f"[.github/workflows/dashboard.yml:19]({RUNNER_WORKSPACE}/.github/workflows/dashboard.yml:19)",
            "`.github/workflows/dashboard.yml:19`",
            id="pr11-link-without-context-becomes-inline-code",
        ),
        pytest.param(
            f"[a.py]({RUNNER_WORKSPACE}/src/a.py#L7-L12)",
            "`src/a.py:7-12`",
            id="line-range-without-context-becomes-inline-code",
        ),
        pytest.param(
            f"[README]({RUNNER_WORKSPACE}/README.md)",
            "`README.md`",
            id="no-line-without-context-becomes-inline-code",
        ),
    ],
)
def test_review_rewrites_local_path_links_to_inline_code_without_context(
    monkeypatch: pytest.MonkeyPatch, body: str, expected: str
) -> None:
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)

    sanitized = codex_pr_assessment.sanitize_review_body(body, "Codex advisory code review")

    assert expected in sanitized
    assert "](" not in sanitized


def test_sanitize_review_cli_reads_repository_and_head_sha_from_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    review = tmp_path / "final-review.md"
    review.write_text(f"[a.py:7]({RUNNER_WORKSPACE}/src/a.py:7)", encoding="utf-8")
    context = tmp_path / "review-context.json"
    context.write_text(json.dumps({"repository": REPO, "pr": {"head_sha": SHA}}), encoding="utf-8")
    output = tmp_path / "out.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex_pr_assessment.py",
            "sanitize-review",
            "--input",
            str(review),
            "--output",
            str(output),
            "--title",
            "Codex advisory code review",
            "--context",
            str(context),
        ],
    )

    assert codex_pr_assessment.main() == 0
    assert f"[a.py:7]({BLOB}/src/a.py#L7)" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param(
            "Codex advisory code review",
            "To rerun this review, comment `/review` as a trusted collaborator, or use the "
            "trigger configured in this repository's Codex review workflow.\n",
            id="code-review-mentions-only-built-in-comment-command",
        ),
        pytest.param(
            "Codex advisory CI analysis",
            "To rerun CI analysis, comment `/codex-ci-assess` as a trusted collaborator.\n",
            id="ci-analysis-unchanged",
        ),
        pytest.param("Codex advisory test", "", id="other-title-has-no-note"),
    ],
)
def test_review_trigger_note_does_not_assume_caller_triggers(title: str, expected: str) -> None:
    note = codex_pr_assessment.review_trigger_note(title)

    assert note == expected
    assert "label" not in note


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
