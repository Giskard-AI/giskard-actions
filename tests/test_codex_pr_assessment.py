import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "codex_pr_assessment.py"
SPEC = importlib.util.spec_from_file_location("codex_pr_assessment", MODULE_PATH)
assert SPEC is not None
codex_pr_assessment = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = codex_pr_assessment
SPEC.loader.exec_module(codex_pr_assessment)


class TestAuthorFiltering(unittest.TestCase):
    def test_ignored_authors(self) -> None:
        for author in (
            {"login": "gemini-code-assist[bot]", "type": "Bot"},
            {"login": "github-actions[bot]", "__typename": "Bot"},
            {"login": "some-gemini-reviewer", "type": "User"},
            None,
        ):
            with self.subTest(author=author):
                self.assertTrue(codex_pr_assessment.is_ignored_author(author))

    def test_human_author_is_not_ignored(self) -> None:
        self.assertFalse(codex_pr_assessment.is_ignored_author({"login": "bazire", "type": "User"}))


class TestIssueCommentTrust(unittest.TestCase):
    def test_trusted_exact_command(self) -> None:
        event = {
            "action": "created",
            "issue": {"pull_request": {}},
            "comment": {"body": "/codex-assess", "author_association": "MEMBER"},
        }

        self.assertTrue(codex_pr_assessment.is_trusted_comment_event(event))

    def test_trusted_ci_command(self) -> None:
        event = {
            "action": "created",
            "issue": {"pull_request": {}},
            "comment": {"body": "/codex-ci-assess", "author_association": "MEMBER"},
        }

        self.assertTrue(codex_pr_assessment.is_trusted_comment_event(event, "/codex-ci-assess"))
        self.assertFalse(codex_pr_assessment.is_trusted_comment_event(event))

    def test_trusted_review_alias_command(self) -> None:
        event = {
            "action": "created",
            "issue": {"pull_request": {}},
            "comment": {"body": "/review", "author_association": "COLLABORATOR"},
        }

        self.assertTrue(
            codex_pr_assessment.is_trusted_comment_event(event, {"/review", "/codex-assess"})
        )
        self.assertFalse(codex_pr_assessment.is_trusted_comment_event(event))

    def test_rejects_untrusted_or_non_pr_comment(self) -> None:
        events = [
            {
                "action": "created",
                "issue": {"pull_request": {}},
                "comment": {"body": "/codex-assess now", "author_association": "MEMBER"},
            },
            {
                "action": "created",
                "issue": {},
                "comment": {"body": "/codex-assess", "author_association": "MEMBER"},
            },
            {
                "action": "created",
                "issue": {"pull_request": {}},
                "comment": {
                    "body": "/codex-assess",
                    "author_association": "CONTRIBUTOR",
                },
            },
        ]

        for event in events:
            with self.subTest(event=event):
                self.assertFalse(codex_pr_assessment.is_trusted_comment_event(event))


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestCodexPrAssessmentWorkflow(unittest.TestCase):
    def test_self_test_uses_local_composites_not_reusables(self) -> None:
        caller = (REPO_ROOT / ".github/workflows/codex-pr-assessment.yml").read_text(
            encoding="utf-8"
        )
        ci = (REPO_ROOT / ".github/workflows/codex-ci-analysis.yml").read_text(encoding="utf-8")
        self.assertIn("uses: ./.github/actions/codex-pr-review", caller)
        self.assertIn("uses: ./.github/actions/codex-post-review", caller)
        self.assertIn("uses: ./.github/actions/codex-ci-collect", ci)
        self.assertIn("uses: ./.github/actions/codex-ci-diagnose", ci)
        self.assertNotIn(".reusable.yml", caller)
        self.assertNotIn(".reusable.yml", ci)
        self.assertNotIn("secrets: inherit", caller)
        self.assertNotIn("secrets: inherit", ci)
        self.assertNotIn("pull_request_target", caller)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", caller)
        self.assertNotIn("GITHUB_WORKFLOW_REF", caller)

    def test_review_composite_loads_base_sha_overlays_not_pr_head(self) -> None:
        review = (REPO_ROOT / ".github/actions/codex-pr-review/action.yml").read_text(
            encoding="utf-8"
        )
        wrapper = (REPO_ROOT / ".github/actions/_lib/run-assess.sh").read_text(encoding="utf-8")
        self.assertNotIn("github.event.pull_request.head.sha", review)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", review)
        self.assertNotIn("GITHUB_WORKFLOW_REF", review)
        self.assertNotIn("path: _giskard_codex", review)
        self.assertNotIn("python3 scripts/ci/codex_pr_assessment.py", review)
        self.assertNotIn("AUTOMATION_APP_PRIVATE_KEY", review)
        self.assertIn("path: trusted-config", review)
        self.assertIn("ref: ${{ steps.resolve.outputs.base_sha }}", review)
        self.assertIn("persist-credentials: false", review)
        self.assertIn("CODEX_REVIEWER_MAP", review)
        self.assertIn("CODEX_ASSESS_REQUIRE_SAME_REPO", review)
        self.assertIn("run-assess.sh", review)
        self.assertIn("ACTION_ROOT", wrapper)
        self.assertNotIn("environment:", review)
        self.assertIn("openai/codex-action@86365089eb2b84e0a8fb0717b304f8bdcb13b20e", review)
        arg_lines = [
            line
            for line in review.splitlines()
            if "codex-args:" in line or line.lstrip().startswith('["--')
        ]
        self.assertTrue(arg_lines)
        self.assertFalse(any("features.multi_agent_v2" in line for line in arg_lines))

    def test_ci_composites_do_not_clone_workflow_sha(self) -> None:
        collect = (REPO_ROOT / ".github/actions/codex-ci-collect/action.yml").read_text(
            encoding="utf-8"
        )
        diagnose = (REPO_ROOT / ".github/actions/codex-ci-diagnose/action.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("CODEX_CI_CONFIG", collect)
        self.assertIn("/codex-ci-assess", collect)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", collect)
        self.assertNotIn("AUTOMATION_APP_PRIVATE_KEY", collect)
        self.assertNotIn("AUTOMATION_APP_PRIVATE_KEY", diagnose)
        self.assertIn("openai/codex-action@86365089eb2b84e0a8fb0717b304f8bdcb13b20e", diagnose)

    def test_notes_action_exports_constraints_without_workspace_project(self) -> None:
        action = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn("uv export --frozen --no-dev --no-hashes --no-emit-project", action)
        self.assertIn("working-directory: ${{ github.action_path }}", action)
        result = subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-hashes",
                "--no-emit-project",
                "--directory",
                str(REPO_ROOT),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("file://", result.stdout)
        self.assertFalse(any(line.startswith("-e ") for line in result.stdout.splitlines()))


class TestReviewContextCollection(unittest.TestCase):
    def test_collect_pr_files_redacts_patch_secret_references(self) -> None:
        original_collect_paginated = codex_pr_assessment.collect_paginated
        codex_pr_assessment.collect_paginated = lambda endpoint: [
            {
                "filename": "backend/example.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
                "patch": "+GITHUB_TOKEN=super-secret-value",
            }
        ]
        try:
            files = codex_pr_assessment.collect_pr_files(
                codex_pr_assessment.GitHubRepo("example", "repo"), 1
            )
        finally:
            codex_pr_assessment.collect_paginated = original_collect_paginated

        self.assertNotIn("super-secret-value", files[0]["patch"])
        self.assertIn("[redacted]", files[0]["patch"])

    def test_collect_issue_comments_redacts_secret_references(self) -> None:
        original_collect_paginated = codex_pr_assessment.collect_paginated
        codex_pr_assessment.collect_paginated = lambda endpoint: [
            {
                "id": 1,
                "user": {"login": "alice", "type": "User"},
                "created_at": "2026-06-12T00:00:00Z",
                "body": "AZURE_AI_API_KEY=super-secret-value",
                "html_url": "https://github.com/example/repo/pull/1#issuecomment-1",
            }
        ]
        try:
            comments = codex_pr_assessment.collect_issue_comments(
                codex_pr_assessment.GitHubRepo("example", "repo"), 1
            )
        finally:
            codex_pr_assessment.collect_paginated = original_collect_paginated

        self.assertNotIn("super-secret-value", comments[0]["body"])
        self.assertIn("[redacted]", comments[0]["body"])

    def test_collect_review_threads_redacts_secret_references(self) -> None:
        original_gh_json = codex_pr_assessment.gh_json
        codex_pr_assessment.gh_json = lambda args: {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "thread-1",
                                    "isResolved": False,
                                    "path": "backend/example.py",
                                    "line": 10,
                                    "startLine": None,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "id": "comment-1",
                                                "body": "GITHUB_TOKEN=super-secret-value",
                                                "createdAt": "2026-06-12T00:00:00Z",
                                                "url": "https://github.com/example/repo/pull/1#discussion_r1",
                                                "author": {
                                                    "login": "alice",
                                                    "__typename": "User",
                                                },
                                            }
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }
        try:
            threads = codex_pr_assessment.collect_review_threads(
                codex_pr_assessment.GitHubRepo("example", "repo"), 1
            )
        finally:
            codex_pr_assessment.gh_json = original_gh_json

        body = threads[0]["comments"][0]["body"]
        self.assertNotIn("super-secret-value", body)
        self.assertIn("[redacted]", body)

    def test_collect_prior_codex_reviews_redacts_secret_references(self) -> None:
        original_collect_paginated = codex_pr_assessment.collect_paginated
        codex_pr_assessment.collect_paginated = lambda endpoint: [
            {
                "id": 1,
                "user": {"login": "github-actions"},
                "submitted_at": "2026-06-12T00:00:00Z",
                "commit_id": "abc",
                "state": "COMMENTED",
                "body": (
                    "## Codex advisory code review\n\n"
                    "Finding: github_pat_abcdefghijklmnopqrstuvwxyz_1234567890"
                ),
                "html_url": "https://github.com/example/repo/pull/1#pullrequestreview-1",
            }
        ]
        try:
            reviews = codex_pr_assessment.collect_prior_codex_reviews(
                codex_pr_assessment.GitHubRepo("example", "repo"), 1
            )
        finally:
            codex_pr_assessment.collect_paginated = original_collect_paginated

        self.assertNotIn("github_pat_", reviews[0]["body"])
        self.assertIn("[redacted]", reviews[0]["body"])


class TestReviewerSelection(unittest.TestCase):
    def test_selects_backend_and_security_reviewers(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            [
                "backend/src/backend/services/auth.py",
                ".github/workflows/ci.yml",
            ]
        )

        self.assertEqual(
            reviewers,
            [
                {
                    "name": "clean-arch-guard",
                    "prompt_file": ".cursor/agents/clean-arch-guard.md",
                    "model_kind": "normal",
                    "role_references": "",
                },
                {
                    "name": "infra-reviewer",
                    "prompt_file": ".cursor/agents/infra-reviewer.md",
                    "model_kind": "normal",
                    "role_references": "",
                },
                {
                    "name": "security-auditor",
                    "prompt_file": ".cursor/agents/security-auditor.md",
                    "model_kind": "critical",
                    "role_references": "",
                },
            ],
        )

    def test_selects_ui_reviewers(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            ["frontend/src/app/p/[projectId]/page.tsx"]
        )

        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["frontend-reviewer", "user-advocate"],
        )

    def test_selects_prompt_critic_for_cursor_agents(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers([".cursor/agents/user-advocate.md"])

        self.assertEqual([reviewer["name"] for reviewer in reviewers], ["prompt-critic"])

    def test_selects_prompt_critic_for_repo_prompt_paths(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            [
                ".cursor/rules/code-quality.mdc",
                ".cursor/skills/slack-message/SKILL.md",
                ".agents/skills/e2e-testing/SKILL.md",
                "codex/skills/example/SKILL.md",
                "AGENTS.md",
            ]
        )

        self.assertEqual([reviewer["name"] for reviewer in reviewers], ["prompt-critic"])

    def test_selects_prompt_critic_for_codex_schemas(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            [".github/codex/schemas/reviewer-output.schema.json"]
        )

        self.assertEqual([reviewer["name"] for reviewer in reviewers], ["prompt-critic"])

    def test_selects_infra_for_nested_docker_files(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            ["deploy/local/Dockerfile", "tools/docker-compose.test.yml"]
        )

        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["infra-reviewer"],
        )

    def test_selects_security_and_infra_for_ci_scripts(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(["scripts/ci/codex_pr_assessment.py"])

        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["infra-reviewer", "security-auditor"],
        )

    def test_selects_security_and_infra_for_dependency_manifests(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            [
                "backend/pyproject.toml",
                "frontend/package.json",
                "azure_oauth_llm_proxy/uv.lock",
                "demo_models/pyproject.toml",
            ]
        )

        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["frontend-reviewer", "infra-reviewer", "security-auditor"],
        )

    def test_selects_migration_reviewer_for_control_migrations(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            ["backend/migrations_control/versions/123_test.py"]
        )

        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["clean-arch-guard", "migration-reviewer"],
        )

    def test_selects_infra_for_repo_infra_roots(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            [
                "db/Dockerfile",
                "keycloak/build_password_blacklist.sh",
                "local-install/package.sh",
                "ssl/create-certs.sh",
            ]
        )

        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["infra-reviewer", "security-auditor"],
        )

    def test_no_match_returns_empty_reviewers(self) -> None:
        self.assertEqual(codex_pr_assessment.select_reviewers(["README.md"]), [])

    def test_selects_python_builder_for_src_python(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(["src/release_notes/config.py"])
        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["python-builder"],
        )

    def test_reviewer_map_overrides_heuristics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            map_path = Path(tmpdir) / "reviewer-map.json"
            map_path.write_text(
                json.dumps(
                    {
                        "rules": [
                            {
                                "name": "prompt-critic",
                                "model_kind": "normal",
                                "path_prefixes": ["src/release_notes/prompts/"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            reviewers = codex_pr_assessment.select_reviewers(
                [
                    "src/release_notes/prompts/release_notes_synthesis.j2",
                    "backend/src/backend/services/auth.py",
                ],
                map_path=map_path,
            )
        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["prompt-critic"],
        )

    def test_reviewer_map_rejects_invalid_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            map_path = Path(tmpdir) / "reviewer-map.json"
            map_path.write_text(
                json.dumps(
                    {
                        "rules": [
                            {
                                "name": "../etc/passwd",
                                "path_prefixes": ["src/"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid reviewer name"):
                codex_pr_assessment.select_reviewers(["src/foo.py"], map_path=map_path)

    def test_parse_role_references_accepts_basenames(self) -> None:
        self.assertEqual(
            codex_pr_assessment.parse_role_references(["backend-architecture.md", ""]),
            ["backend-architecture.md"],
        )

    def test_parse_role_references_rejects_paths(self) -> None:
        for value in ("../x.md", "a/b.md", "/abs.md", ".."):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid role reference"):
                    codex_pr_assessment.parse_role_references([value])

    def test_example_hub_map_emits_role_references(self) -> None:
        reviewers = {
            item["name"]: item["role_references"]
            for item in codex_pr_assessment.select_reviewers(
                ["backend/src/backend/services/auth.py"],
                map_path=REPO_ROOT / "examples" / "llm-hub.reviewer-map.json",
            )
        }
        self.assertEqual(reviewers["clean-arch-guard"], "backend-architecture.md")
        self.assertEqual(reviewers["security-auditor"], "security-patterns.md")

    def test_stage_overlay_copies_allowlisted_basenames_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trusted = tmp / "trusted-config"
            agents = trusted / ".cursor" / "agents"
            refs = agents / "references"
            refs.mkdir(parents=True)
            (agents / "clean-arch-guard.md").write_text("role overlay", encoding="utf-8")
            (refs / "backend-architecture.md").write_text("architecture", encoding="utf-8")
            (refs / "secret.md").write_text("do not copy", encoding="utf-8")
            dest = tmp / "assessment" / "reviewer-instructions.md"
            refs_out = tmp / "assessment" / "role-references"
            args = argparse.Namespace(
                name="clean-arch-guard",
                trusted_config=str(trusted),
                output=str(dest),
                references_output=str(refs_out),
                role_references="backend-architecture.md",
            )
            self.assertEqual(codex_pr_assessment.main_stage_overlay(args), 0)
            self.assertEqual(dest.read_text(encoding="utf-8"), "role overlay")
            self.assertEqual(
                (refs_out / "backend-architecture.md").read_text(encoding="utf-8"),
                "architecture",
            )
            self.assertFalse((refs_out / "secret.md").exists())

    def test_repo_reviewer_map_covers_action_and_prompts(self) -> None:
        reviewers = codex_pr_assessment.select_reviewers(
            ["action.yml", "src/release_notes/prompts/release_notes_synthesis.j2"],
            map_path=REPO_ROOT / ".github/codex/reviewer-map.json",
        )
        self.assertEqual(
            [reviewer["name"] for reviewer in reviewers],
            ["infra-reviewer", "prompt-critic", "security-auditor"],
        )


class TestEventResolution(unittest.TestCase):
    def test_resolve_workflow_run_ignores_non_pr_events(self) -> None:
        original_event_name = codex_pr_assessment.os.environ.get("GITHUB_EVENT_NAME")
        codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = "workflow_run"
        event = {"workflow_run": {"event": "push", "head_sha": "abc"}}
        repo = codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub")

        try:
            self.assertIsNone(codex_pr_assessment.resolve_pr_number(event, repo))
        finally:
            if original_event_name is None:
                codex_pr_assessment.os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = original_event_name

    def test_resolve_workflow_run_is_disabled_by_default(self) -> None:
        original_event_name = codex_pr_assessment.os.environ.get("GITHUB_EVENT_NAME")
        original_allow = codex_pr_assessment.os.environ.get("CODEX_ASSESS_ALLOW_WORKFLOW_RUN")
        codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = "workflow_run"
        codex_pr_assessment.os.environ.pop("CODEX_ASSESS_ALLOW_WORKFLOW_RUN", None)
        event = {
            "workflow_run": {
                "event": "pull_request",
                "head_sha": "abc",
                "pull_requests": [{"number": 123}],
            }
        }
        repo = codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub")

        try:
            self.assertIsNone(codex_pr_assessment.resolve_pr_number(event, repo))
        finally:
            if original_event_name is None:
                codex_pr_assessment.os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = original_event_name
            if original_allow is None:
                codex_pr_assessment.os.environ.pop("CODEX_ASSESS_ALLOW_WORKFLOW_RUN", None)
            else:
                codex_pr_assessment.os.environ["CODEX_ASSESS_ALLOW_WORKFLOW_RUN"] = original_allow

    def test_resolve_trusted_ci_comment_event(self) -> None:
        original_event_name = codex_pr_assessment.os.environ.get("GITHUB_EVENT_NAME")
        original_commands = codex_pr_assessment.os.environ.get("CODEX_ASSESS_COMMENT_COMMANDS")
        original_command = codex_pr_assessment.os.environ.get("CODEX_ASSESS_COMMENT_COMMAND")
        codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = "issue_comment"
        codex_pr_assessment.os.environ.pop("CODEX_ASSESS_COMMENT_COMMANDS", None)
        codex_pr_assessment.os.environ["CODEX_ASSESS_COMMENT_COMMAND"] = "/codex-ci-assess"
        event = {
            "action": "created",
            "issue": {"number": 123, "pull_request": {}},
            "comment": {
                "body": "/codex-ci-assess",
                "author_association": "COLLABORATOR",
            },
        }

        try:
            self.assertEqual(
                codex_pr_assessment.resolve_pr_number(
                    event,
                    codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                ),
                123,
            )
        finally:
            if original_event_name is None:
                codex_pr_assessment.os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = original_event_name
            if original_commands is None:
                codex_pr_assessment.os.environ.pop("CODEX_ASSESS_COMMENT_COMMANDS", None)
            else:
                codex_pr_assessment.os.environ["CODEX_ASSESS_COMMENT_COMMANDS"] = original_commands
            if original_command is None:
                codex_pr_assessment.os.environ.pop("CODEX_ASSESS_COMMENT_COMMAND", None)
            else:
                codex_pr_assessment.os.environ["CODEX_ASSESS_COMMENT_COMMAND"] = original_command

    def test_resolve_workflow_dispatch_event(self) -> None:
        original_event_name = codex_pr_assessment.os.environ.get("GITHUB_EVENT_NAME")
        codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        try:
            self.assertEqual(
                codex_pr_assessment.resolve_pr_number(
                    {"inputs": {"pr_number": "123"}},
                    codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                ),
                123,
            )
        finally:
            if original_event_name is None:
                codex_pr_assessment.os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = original_event_name

    def test_resolve_pull_request_event(self) -> None:
        original_event_name = codex_pr_assessment.os.environ.get("GITHUB_EVENT_NAME")
        codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = "pull_request"
        try:
            self.assertEqual(
                codex_pr_assessment.resolve_pr_number(
                    {"pull_request": {"number": 123}},
                    codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                ),
                123,
            )
        finally:
            if original_event_name is None:
                codex_pr_assessment.os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                codex_pr_assessment.os.environ["GITHUB_EVENT_NAME"] = original_event_name

    def test_same_repo_pr_detection(self) -> None:
        self.assertTrue(
            codex_pr_assessment.is_same_repo_pr(
                {
                    "head": {"repo": {"full_name": "Giskard-AI/llm-hub"}},
                    "base": {"repo": {"full_name": "Giskard-AI/llm-hub"}},
                }
            )
        )
        self.assertFalse(
            codex_pr_assessment.is_same_repo_pr(
                {
                    "head": {"repo": {"full_name": "someone/fork"}},
                    "base": {"repo": {"full_name": "Giskard-AI/llm-hub"}},
                }
            )
        )


class TestContextSeparation(unittest.TestCase):
    def test_review_context_excludes_ci_fields(self) -> None:
        original_repo = codex_pr_assessment.repo_from_env
        original_get_pr = codex_pr_assessment.get_pr
        original_files = codex_pr_assessment.collect_pr_files
        original_comments = codex_pr_assessment.collect_issue_comments
        original_threads = codex_pr_assessment.collect_review_threads
        original_codex_reviews = codex_pr_assessment.collect_prior_codex_reviews

        codex_pr_assessment.repo_from_env = lambda: codex_pr_assessment.GitHubRepo(
            owner="Giskard-AI", name="llm-hub"
        )
        codex_pr_assessment.get_pr = lambda repo, pr: {
            "title": "Test",
            "state": "open",
            "draft": False,
            "base": {"ref": "dev", "sha": "base"},
            "head": {"ref": "branch", "sha": "head"},
            "html_url": "https://example.com/pr",
            "user": {"login": "alice"},
        }
        codex_pr_assessment.collect_pr_files = lambda repo, pr: [
            {"filename": "backend/a.py", "patch": "@@ patch"}
        ]
        codex_pr_assessment.collect_issue_comments = lambda repo, pr: [{"body": "human"}]
        codex_pr_assessment.collect_review_threads = lambda repo, pr: [{"id": "thread"}]
        codex_pr_assessment.collect_prior_codex_reviews = lambda repo, pr: [
            {"body": "## Codex advisory code review"}
        ]

        try:
            context = codex_pr_assessment.collect_review_context(1)
        finally:
            codex_pr_assessment.repo_from_env = original_repo
            codex_pr_assessment.get_pr = original_get_pr
            codex_pr_assessment.collect_pr_files = original_files
            codex_pr_assessment.collect_issue_comments = original_comments
            codex_pr_assessment.collect_review_threads = original_threads
            codex_pr_assessment.collect_prior_codex_reviews = original_codex_reviews

        self.assertIn("files", context)
        self.assertIn("prior_codex_reviews", context)
        self.assertNotIn("workflow_runs", context)
        self.assertNotIn("check_runs", context)
        self.assertNotIn("failed_logs", context)
        self.assertNotIn("sonar", context)

    def test_ci_context_excludes_review_comments_and_patches(self) -> None:
        original_repo = codex_pr_assessment.repo_from_env
        original_get_pr = codex_pr_assessment.get_pr
        original_files = codex_pr_assessment.collect_pr_files
        original_workflows = codex_pr_assessment.workflow_runs_for_sha
        original_key_workflows = codex_pr_assessment.latest_workflow_runs
        original_checks = codex_pr_assessment.collect_check_runs
        original_logs = codex_pr_assessment.collect_failed_logs
        original_sonar = codex_pr_assessment.collect_sonar

        codex_pr_assessment.repo_from_env = lambda: codex_pr_assessment.GitHubRepo(
            owner="Giskard-AI", name="llm-hub"
        )
        codex_pr_assessment.get_pr = lambda repo, pr: {
            "title": "Test",
            "state": "open",
            "draft": False,
            "base": {"ref": "dev", "sha": "base"},
            "head": {"ref": "branch", "sha": "head"},
            "html_url": "https://example.com/pr",
            "user": {"login": "alice"},
        }
        codex_pr_assessment.collect_pr_files = lambda repo, pr: [
            {"filename": "frontend/a.tsx", "status": "modified", "patch": "@@ patch"}
        ]
        codex_pr_assessment.workflow_runs_for_sha = lambda repo, sha: [
            {"name": "Full CI", "status": "completed", "conclusion": "success"}
        ]
        codex_pr_assessment.latest_workflow_runs = lambda repo, sha, workflow_names=(): {
            "Full CI": {"name": "Full CI", "status": "completed", "conclusion": "success"}
        }
        codex_pr_assessment.collect_check_runs = lambda repo, sha: []
        codex_pr_assessment.collect_failed_logs = lambda repo, runs: []
        codex_pr_assessment.collect_sonar = lambda repo, pr: None

        try:
            context = codex_pr_assessment.collect_ci_context(1)
        finally:
            codex_pr_assessment.repo_from_env = original_repo
            codex_pr_assessment.get_pr = original_get_pr
            codex_pr_assessment.collect_pr_files = original_files
            codex_pr_assessment.workflow_runs_for_sha = original_workflows
            codex_pr_assessment.latest_workflow_runs = original_key_workflows
            codex_pr_assessment.collect_check_runs = original_checks
            codex_pr_assessment.collect_failed_logs = original_logs
            codex_pr_assessment.collect_sonar = original_sonar

        self.assertIn("workflow_runs", context)
        self.assertIn("check_runs", context)
        self.assertNotIn("comments", context)
        self.assertNotIn("review_threads", context)
        self.assertEqual(context["files"], [{"filename": "frontend/a.tsx", "status": "modified"}])


class TestPagination(unittest.TestCase):
    def test_collect_paginated_requires_explicit_key_for_object_pages(self) -> None:
        original = codex_pr_assessment.gh_json
        codex_pr_assessment.gh_json = lambda *args, **kwargs: [{"items": [], "other": []}]
        try:
            with self.assertRaisesRegex(ValueError, "list_key"):
                codex_pr_assessment.collect_paginated("test")
        finally:
            codex_pr_assessment.gh_json = original

    def test_collect_paginated_flattens_selected_list_key(self) -> None:
        original = codex_pr_assessment.gh_json
        codex_pr_assessment.gh_json = lambda *args, **kwargs: [
            {"check_runs": [{"id": 1}], "other": [{"bad": True}]},
            {"check_runs": [{"id": 2}], "other": [{"bad": True}]},
        ]
        try:
            self.assertEqual(
                codex_pr_assessment.collect_paginated("test", list_key="check_runs"),
                [{"id": 1}, {"id": 2}],
            )
        finally:
            codex_pr_assessment.gh_json = original

    def test_collect_check_runs_uses_paginated_check_runs(self) -> None:
        original_paginated = codex_pr_assessment.collect_paginated
        original_annotations = codex_pr_assessment.collect_check_annotations

        def fake_paginated(endpoint, *, list_key=None):
            self.assertEqual(list_key, "check_runs")
            return [
                {
                    "id": 1,
                    "name": "lint",
                    "status": "completed",
                    "conclusion": "success",
                    "details_url": "https://example.com/details",
                    "html_url": "https://example.com/html",
                },
                {
                    "id": 2,
                    "name": "test",
                    "status": "completed",
                    "conclusion": "failure",
                    "details_url": "https://example.com/details",
                    "html_url": "https://example.com/html",
                },
            ]

        codex_pr_assessment.collect_paginated = fake_paginated
        codex_pr_assessment.collect_check_annotations = lambda repo, check_id: [
            {"path": "x.py", "message": "failed"}
        ]
        try:
            checks = codex_pr_assessment.collect_check_runs(
                codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                "abc",
            )
        finally:
            codex_pr_assessment.collect_paginated = original_paginated
            codex_pr_assessment.collect_check_annotations = original_annotations

        self.assertEqual(checks[0]["annotations"], [])
        self.assertEqual(checks[1]["annotations"], [{"path": "x.py", "message": "failed"}])

    def test_collect_check_annotations_redacts_messages(self) -> None:
        original_paginated = codex_pr_assessment.collect_paginated
        codex_pr_assessment.collect_paginated = lambda endpoint: [
            {
                "path": "x.py",
                "start_line": 1,
                "end_line": 1,
                "annotation_level": "failure",
                "message": "AZURE_AI_API_KEY=super-secret",
            }
        ]
        try:
            annotations = codex_pr_assessment.collect_check_annotations(
                codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                123,
            )
        finally:
            codex_pr_assessment.collect_paginated = original_paginated

        self.assertIn("[redacted]", annotations[0]["message"])
        self.assertNotIn("super-secret", annotations[0]["message"])


class TestSonarCollection(unittest.TestCase):
    def test_collect_sonar_redacts_issue_messages(self) -> None:
        original_urlopen = codex_pr_assessment.urlopen
        original_token = codex_pr_assessment.os.environ.get("SONAR_QUBE_TOKEN")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "total": 1,
                        "issues": [
                            {
                                "type": "VULNERABILITY",
                                "severity": "MAJOR",
                                "component": "Giskard-AI_llm-hub:scripts/ci/x.py",
                                "line": 1,
                                "rule": "test",
                                "message": "GH_TOKEN=super-secret",
                            }
                        ],
                    }
                ).encode()

        codex_pr_assessment.urlopen = lambda request, timeout: FakeResponse()
        codex_pr_assessment.os.environ["SONAR_QUBE_TOKEN"] = "token"
        try:
            sonar = codex_pr_assessment.collect_sonar(
                codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                1634,
            )
        finally:
            codex_pr_assessment.urlopen = original_urlopen
            if original_token is None:
                codex_pr_assessment.os.environ.pop("SONAR_QUBE_TOKEN", None)
            else:
                codex_pr_assessment.os.environ["SONAR_QUBE_TOKEN"] = original_token

        assert sonar is not None
        self.assertIn("[redacted]", sonar["issues"][0]["message"])
        self.assertNotIn("super-secret", sonar["issues"][0]["message"])


class TestGitHubText(unittest.TestCase):
    def test_gh_text_returns_stderr_for_tolerated_failure(self) -> None:
        original_run = codex_pr_assessment.subprocess.run

        def fake_run(command, *, check, stdout, stderr, text):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing run")

        codex_pr_assessment.subprocess.run = fake_run
        try:
            self.assertEqual(codex_pr_assessment.gh_text(["bad"], check=False), "missing run")
        finally:
            codex_pr_assessment.subprocess.run = original_run

    def test_gh_text_raises_with_stderr_for_checked_failure(self) -> None:
        original_run = codex_pr_assessment.subprocess.run

        def fake_run(command, *, check, stdout, stderr, text):
            return subprocess.CompletedProcess(command, 1, stdout="out", stderr="err")

        codex_pr_assessment.subprocess.run = fake_run
        try:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                codex_pr_assessment.gh_text(["bad"])
        finally:
            codex_pr_assessment.subprocess.run = original_run

        self.assertEqual(raised.exception.stdout, "out")
        self.assertEqual(raised.exception.stderr, "err")


class TestPriorCodexReviews(unittest.TestCase):
    def test_collect_prior_codex_reviews_keeps_only_codex_advisory_reviews(self) -> None:
        original_paginated = codex_pr_assessment.collect_paginated
        codex_pr_assessment.collect_paginated = lambda endpoint: [
            {
                "id": 1,
                "body": "## Codex advisory code review\n\nKnown finding",
                "user": {"login": "github-actions[bot]"},
                "submitted_at": "2026-06-11T10:00:00Z",
                "commit_id": "abc",
                "state": "COMMENTED",
                "html_url": "https://github.com/example/review",
            },
            {
                "id": 2,
                "body": "## Code Review\n\nGemini",
                "user": {"login": "gemini-code-assist[bot]"},
            },
            {
                "id": 3,
                "body": "## Codex advisory code review\n\nSpoof",
                "user": {"login": "someone"},
            },
        ]
        try:
            reviews = codex_pr_assessment.collect_prior_codex_reviews(
                codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub"),
                1634,
            )
        finally:
            codex_pr_assessment.collect_paginated = original_paginated

        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["id"], 1)
        self.assertIn("Known finding", reviews[0]["body"])


class TestFailedLogCollection(unittest.TestCase):
    def test_failed_logs_are_redacted_before_context_write(self) -> None:
        original_gh_text = codex_pr_assessment.gh_text
        repo = codex_pr_assessment.GitHubRepo(owner="Giskard-AI", name="llm-hub")

        def fake_gh_text(args: list[str], *, check: bool = True) -> str:
            self.assertFalse(check)
            return "AZURE_AI_API_KEY=super-secret\nnormal failure"

        codex_pr_assessment.gh_text = fake_gh_text
        try:
            logs = codex_pr_assessment.collect_failed_logs(
                repo,
                [{"id": 1, "name": "Full CI", "conclusion": "failure"}],
            )
        finally:
            codex_pr_assessment.gh_text = original_gh_text

        self.assertEqual(len(logs), 1)
        self.assertIn("[redacted]", logs[0]["log_excerpt"])
        self.assertNotIn("super-secret", logs[0]["log_excerpt"])


class TestCiTriage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hub_ci_config = json.loads(
            (REPO_ROOT / "examples" / "llm-hub.ci-config.json").read_text(encoding="utf-8")
        )

    def _triage(self, context: dict) -> dict:
        return codex_pr_assessment.triage_ci({**context, "ci_config": self.hub_ci_config})

    def test_classifies_common_failure_modes(self) -> None:
        cases = [
            ("pytest", "AssertionError: failed", "failure", "test_failure"),
            ("lint", "ruff failed", "failure", "lint_failure"),
            ("frontend build", "pnpm build failed", "failure", "build_failure"),
            ("playwright", "browser closed", "failure", "e2e_failure"),
            ("setup", "docker network failed", "failure", "infra_failure"),
            ("slow job", "timed out", "timed_out", "timeout"),
            ("cancelled", "", "cancelled", "cancelled"),
            ("mystery", "no matching pattern", "failure", "unknown"),
        ]

        for name, text, conclusion, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    codex_pr_assessment.classify_ci_evidence(name, text, conclusion),
                    expected,
                )

    def test_triage_recommends_missing_and_rerunnable_ci(self) -> None:
        triage = self._triage(
            {
                "pr": {"head_sha": "abc"},
                "files": [{"filename": "frontend/src/app/page.tsx"}],
                "key_workflow_runs": {
                    "Full CI": {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "cancelled",
                    },
                    "End-to-end frontend tests": {
                        "name": "End-to-end frontend tests",
                        "status": "not_run",
                        "conclusion": "skipped",
                    },
                },
                "workflow_runs": [
                    {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "cancelled",
                    }
                ],
                "check_runs": [],
                "failed_logs": [],
                "sonar": None,
            }
        )

        self.assertFalse(triage["ready"])
        self.assertTrue(triage["needs_ai_diagnosis"])
        self.assertIn(
            {
                "action": "rerun_workflow",
                "target": "Full CI",
                "reason": "Full CI concluded with cancelled.",
            },
            triage["additional_ci_recommendations"],
        )
        self.assertIn(
            {
                "action": "run_workflow",
                "target": "End-to-end frontend tests",
                "reason": "End-to-end frontend tests has no run for this PR head SHA.",
            },
            triage["additional_ci_recommendations"],
        )

    def test_triage_ready_requires_terminal_key_workflows(self) -> None:
        triage = self._triage(
            {
                "pr": {"head_sha": "abc"},
                "files": [],
                "key_workflow_runs": {
                    "Full CI": {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    "End-to-end frontend tests": {
                        "name": "End-to-end frontend tests",
                        "status": "not_run",
                        "conclusion": "skipped",
                    },
                },
                "workflow_runs": [],
                "check_runs": [],
                "failed_logs": [],
                "sonar": None,
            }
        )

        self.assertFalse(triage["ready"])

    def test_triage_recommends_package_ci_for_manifest_changes(self) -> None:
        triage = self._triage(
            {
                "pr": {"head_sha": "abc"},
                "files": [
                    {"filename": "backend/pyproject.toml"},
                    {"filename": "frontend/pnpm-lock.yaml"},
                    {"filename": "azure_oauth_llm_proxy/uv.lock"},
                    {"filename": "demo_models/pyproject.toml"},
                ],
                "key_workflow_runs": {
                    "Full CI": {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    "End-to-end frontend tests": {
                        "name": "End-to-end frontend tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                },
                "workflow_runs": [
                    {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "End-to-end frontend tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
                "check_runs": [],
                "failed_logs": [],
                "sonar": None,
            }
        )

        self.assertIn(
            {
                "action": "apply_label",
                "target": "test-package",
                "reason": "Package-related files changed and the label-triggered local package workflow has no run for this head SHA.",
            },
            triage["additional_ci_recommendations"],
        )

    def test_triage_recommends_local_eval_stress_label_for_local_eval_changes(self) -> None:
        triage = self._triage(
            {
                "pr": {"head_sha": "abc"},
                "files": [{"filename": "backend/src/backend/services/evaluation.py"}],
                "key_workflow_runs": {
                    "Full CI": {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    "End-to-end frontend tests": {
                        "name": "End-to-end frontend tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                },
                "workflow_runs": [
                    {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "End-to-end frontend tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
                "check_runs": [],
                "failed_logs": [],
                "sonar": None,
            }
        )

        self.assertIn(
            {
                "action": "apply_label",
                "target": "local-eval-stress",
                "reason": "Local evaluation submission code changed and the label-triggered local-eval stress workflow has no run for this head SHA.",
            },
            triage["additional_ci_recommendations"],
        )

    def test_triage_skips_local_eval_stress_label_when_workflow_ran(self) -> None:
        triage = self._triage(
            {
                "pr": {"head_sha": "abc"},
                "files": [{"filename": "backend/src/backend/services/evaluation.py"}],
                "key_workflow_runs": {
                    "Full CI": {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    "End-to-end frontend tests": {
                        "name": "End-to-end frontend tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                },
                "workflow_runs": [
                    {
                        "name": "Full CI",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "End-to-end frontend tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "Local eval output stress test",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
                "check_runs": [],
                "failed_logs": [],
                "sonar": None,
            }
        )

        self.assertNotIn(
            {
                "action": "apply_label",
                "target": "local-eval-stress",
                "reason": "Local evaluation submission code changed and the label-triggered local-eval stress workflow has no run for this head SHA.",
            },
            triage["additional_ci_recommendations"],
        )


class TestEndpointPreflight(unittest.TestCase):
    def test_accepts_responses_compatible_base(self) -> None:
        cases = {
            "https://example.openai.azure.com/openai/v1": "https://example.openai.azure.com/openai/v1/responses",
            "https://example.openai.azure.com/openai/v1/responses": "https://example.openai.azure.com/openai/v1/responses",
            "https://example.services.ai.azure.com/api/projects/ci": (
                "https://example.services.ai.azure.com/api/projects/ci/openai/v1/responses"
            ),
            "https://example.services.ai.azure.com/api/projects/ci/openai/v1/responses": (
                "https://example.services.ai.azure.com/api/projects/ci/openai/v1/responses"
            ),
        }

        for base_url, expected in cases.items():
            with self.subTest(base_url=base_url):
                self.assertEqual(codex_pr_assessment.ensure_responses_endpoint(base_url), expected)

    def test_rejects_non_azure_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "allowlist"):
            codex_pr_assessment.ensure_responses_endpoint("https://evil.example/openai/v1")

    def test_rejects_non_responses_base(self) -> None:
        with self.assertRaisesRegex(ValueError, "Responses-compatible"):
            codex_pr_assessment.ensure_responses_endpoint("https://example.openai.azure.com/models")

    def test_rejects_plaintext_responses_base(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute HTTPS URL"):
            codex_pr_assessment.ensure_responses_endpoint(
                "http://example.openai.azure.com/openai/v1"
            )


class TestAzureModelCatalog(unittest.TestCase):
    def test_patch_disables_responses_lite_for_gpt_56_models(self) -> None:
        catalog = {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "prefer_websockets": True,
                    "use_responses_lite": False,
                    "multi_agent_version": None,
                    "tool_mode": None,
                },
                {
                    "slug": "gpt-5.6-terra",
                    "prefer_websockets": True,
                    "use_responses_lite": True,
                    "multi_agent_version": "v2",
                    "tool_mode": "code_mode_only",
                },
                {
                    "slug": "gpt-5.6-sol",
                    "use_responses_lite": True,
                    "multi_agent_version": "v2",
                    "tool_mode": "code_mode_only",
                },
                {
                    "slug": "gpt-5.6-luna",
                    "use_responses_lite": True,
                    "multi_agent_version": "v1",
                    "tool_mode": "code_mode_only",
                },
            ]
        }

        patched = codex_pr_assessment.patch_azure_model_catalog(catalog)

        self.assertEqual(
            patched["models"][0],
            {
                "slug": "gpt-5.5",
                "prefer_websockets": True,
                "use_responses_lite": False,
                "multi_agent_version": None,
                "tool_mode": None,
            },
        )
        self.assertEqual(
            patched["models"][1],
            {
                "slug": "gpt-5.6-terra",
                "prefer_websockets": True,
                "use_responses_lite": False,
                "multi_agent_version": None,
                "tool_mode": None,
            },
        )
        self.assertEqual(
            patched["models"][2],
            {
                "slug": "gpt-5.6-sol",
                "use_responses_lite": False,
                "multi_agent_version": None,
                "tool_mode": None,
            },
        )
        self.assertEqual(
            patched["models"][3],
            {
                "slug": "gpt-5.6-luna",
                "use_responses_lite": False,
                "multi_agent_version": None,
                "tool_mode": None,
            },
        )

    def test_patch_requires_used_gpt_56_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, r"gpt-5\.6-luna, gpt-5\.6-terra"):
            codex_pr_assessment.patch_azure_model_catalog(
                {"models": [{"slug": "gpt-5.5", "use_responses_lite": False}]}
            )

    def test_prepare_command_writes_patched_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            catalog_path = tmp / "catalog.json"
            output_path = tmp / "patched.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-5.6-terra",
                                "use_responses_lite": True,
                                "multi_agent_version": "v2",
                                "tool_mode": "code_mode_only",
                            },
                            {
                                "slug": "gpt-5.6-luna",
                                "use_responses_lite": True,
                                "multi_agent_version": "v1",
                                "tool_mode": "code_mode_only",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            exit_code = codex_pr_assessment.main_prepare_azure_catalog(
                argparse.Namespace(catalog=str(catalog_path), output=str(output_path))
            )

            self.assertEqual(exit_code, 0)
            patched = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(patched["models"][0]["slug"], "gpt-5.6-terra")
            self.assertFalse(patched["models"][0]["use_responses_lite"])
            self.assertIsNone(patched["models"][0]["multi_agent_version"])
            self.assertIsNone(patched["models"][0]["tool_mode"])

    def test_vendored_catalog_disables_responses_lite_for_used_models(self) -> None:
        catalog = codex_pr_assessment.load_json(codex_pr_assessment.VENDORED_AZURE_MODEL_CATALOG)
        by_slug = {model["slug"]: model for model in catalog["models"]}
        for slug in codex_pr_assessment.REQUIRED_AZURE_CATALOG_SLUGS:
            with self.subTest(slug=slug):
                model = by_slug[slug]
                self.assertFalse(model["use_responses_lite"])
                self.assertIsNone(model["multi_agent_version"])
                self.assertIsNone(model["tool_mode"])


class TestReviewSanitizer(unittest.TestCase):
    def test_sanitizer_adds_header_and_defangs_untrusted_content(self) -> None:
        body = (
            "Please ping @team/security.\n"
            "Allowed: https://github.com/Giskard-AI/llm-hub/actions/runs/1\n"
            "Bad: https://evil.example/phish\n"
            "AZURE_AI_API_KEY=super-secret\n"
        )

        sanitized = codex_pr_assessment.sanitize_review_body(body, "Codex advisory test")

        self.assertTrue(sanitized.startswith("## Codex advisory test"))
        self.assertIn("(at)team/security", sanitized)
        self.assertIn("https://github.com/Giskard-AI/llm-hub/actions/runs/1", sanitized)
        self.assertIn("[external-link-removed]", sanitized)
        self.assertNotIn("super-secret", sanitized)

    def test_code_review_sanitizer_includes_rerun_instructions(self) -> None:
        sanitized = codex_pr_assessment.sanitize_review_body(
            "No findings.", "Codex advisory code review"
        )

        self.assertIn("`codex-review` label", sanitized)
        self.assertIn("`/review`", sanitized)

    def test_ci_sanitizer_includes_rerun_instructions(self) -> None:
        sanitized = codex_pr_assessment.sanitize_review_body(
            "No failures.", "Codex advisory CI analysis"
        )

        self.assertIn("`/codex-ci-assess`", sanitized)

    def test_sanitizer_removes_malformed_ipv6_url_without_crashing(self) -> None:
        sanitized = codex_pr_assessment.sanitize_review_body(
            "Bad: https://[2001:db8::1]:8443/run", "Codex advisory test"
        )

        self.assertIn("[external-link-removed]", sanitized)

    def test_redacts_common_secret_shapes(self) -> None:
        body = "\n".join(
            [
                "GITHUB_TOKEN=github-token-value",
                "MY_SECRET=secret-value",
                "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
                "ghp_abcdefghijklmnopqrstuvwxyz123456",
                "github_pat_abcdefghijklmnopqrstuvwxyz_1234567890",
                "AKIAABCDEFGHIJKLMNOP",
                "eyJabcdefghijkl.eyJmnopqrstuvwxyz.abcdefghijklmnopqrstuvwxyz",
                "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
            ]
        )

        sanitized = codex_pr_assessment.redact_secret_references(body)

        self.assertNotIn("github-token-value", sanitized)
        self.assertNotIn("secret-value", sanitized)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", sanitized)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", sanitized)
        self.assertNotIn("PRIVATE KEY", sanitized)
        self.assertIn("[redacted]", sanitized)


class TestSelectorOutput(unittest.TestCase):
    def test_select_command_writes_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            context_path = tmp / "context.json"
            output_path = tmp / "reviewers.json"
            github_output = tmp / "github-output"
            context_path.write_text(
                json.dumps({"files": [{"filename": "backend/migrations/123_test.py"}]}),
                encoding="utf-8",
            )

            context = codex_pr_assessment.load_json(context_path)
            reviewers = codex_pr_assessment.select_reviewers(
                [item["filename"] for item in context["files"]]
            )
            codex_pr_assessment.write_json(output_path, {"reviewers": reviewers})
            codex_pr_assessment.append_github_output(
                github_output,
                {
                    "has_reviewers": "true" if reviewers else "false",
                    "matrix": json.dumps({"include": reviewers}, separators=(",", ":")),
                },
            )

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8"))["reviewers"],
                [
                    {
                        "name": "clean-arch-guard",
                        "prompt_file": ".cursor/agents/clean-arch-guard.md",
                        "model_kind": "normal",
                        "role_references": "",
                    },
                    {
                        "name": "migration-reviewer",
                        "prompt_file": ".cursor/agents/migration-reviewer.md",
                        "model_kind": "critical",
                        "role_references": "",
                    },
                ],
            )
            self.assertIn("has_reviewers=true", github_output.read_text(encoding="utf-8"))


class TestAggregatorPrompt(unittest.TestCase):
    def test_prompt_preserves_remaining_valid_reviewer_findings(self) -> None:
        prompt = (REPO_ROOT / ".github" / "codex" / "prompts" / "pr-aggregator.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("carry forward every remaining valid reviewer finding", prompt)
        self.assertIn("do not drop valid findings for brevity", prompt)
        self.assertIn("keep/reject", prompt)


if __name__ == "__main__":
    unittest.main()
