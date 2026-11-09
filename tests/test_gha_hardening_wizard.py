import importlib.util
from pathlib import Path

import pytest

PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "gha-hardening"
    / "scripts"
    / "setup_github_app.py"
)
spec = importlib.util.spec_from_file_location("setup_github_app", PATH)
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)

FULL = [
    "--org", "Giskard-AI", "--name", "svc-release", "--repo", "Giskard-AI/svc",
    "--permission", "contents=write", "--environment", "release", "--branch", "main",
    "--client-id-variable", "RELEASE_APP_CLIENT_ID",
    "--private-key-secret", "RELEASE_APP_PRIVATE_KEY",
]  # fmt: skip


def args(*extra: str):
    return wizard.parse_args([*FULL, *extra])


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        pytest.param([], {"metadata": "read"}, id="metadata-always-read"),
        pytest.param(
            ["contents=write", "pull_requests=read"],
            {"metadata": "read", "contents": "write", "pull_requests": "read"},
            id="repeatable-permissions",
        ),
    ],
)
def test_parse_permissions(values, expected):
    assert wizard.parse_permissions(values) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("contents", id="missing-level"),
        pytest.param("contents=all", id="unknown-level"),
    ],
)
def test_bad_permission_is_rejected(value):
    with pytest.raises(wizard.WizardError):
        wizard.parse_permissions([value])


@pytest.mark.parametrize(
    ("override", "message"),
    [
        pytest.param(["--name", "my app"], "App name", id="name-with-space"),
        pytest.param(["--client-id-variable", "client_id"], "UPPER_SNAKE_CASE", id="lower-var"),
        pytest.param(["--repo", "Other/svc"], "not in Giskard-AI", id="repo-outside-org"),
    ],
)
def test_validation(override, message):
    with pytest.raises(wizard.WizardError, match=message):
        wizard.validate(args(*override))


def test_manifest_is_private_without_webhook():
    manifest = wizard.build_manifest(args(), "http://127.0.0.1:1/callback")
    assert manifest["public"] is False
    assert manifest["hook_attributes"]["active"] is False
    assert manifest["default_events"] == []
    assert manifest["default_permissions"] == {"metadata": "read", "contents": "write"}


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        pytest.param([], [{"actor_id": 7, "actor_type": "Integration", "bypass_mode": "always"}],
                     id="added"),
        pytest.param(
            [{"actor_id": 7, "actor_type": "Integration", "bypass_mode": "pull_request"},
             {"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request"}],
            [{"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "pull_request"},
             {"actor_id": 7, "actor_type": "Integration", "bypass_mode": "always"}],
            id="replaced-not-duplicated",
        ),
    ],
)  # fmt: skip
def test_with_bypass(existing, expected):
    ruleset = {
        "id": 3,
        "name": "main",
        "enforcement": "active",
        "rules": [],
        "bypass_actors": existing,
    }
    body = wizard.with_bypass(ruleset, 7, "always")
    assert body["bypass_actors"] == expected
    assert "id" not in body


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        pytest.param([], "Install svc-release on Giskard-AI/svc only", id="installed-where-used"),
        pytest.param(
            ["--install-repo", "Giskard-AI/lib"],
            "Install svc-release on Giskard-AI/lib only",
            id="installed-on-the-cloned-repo",
        ),
    ],
)
def test_install_target(extra, expected):
    assert expected in "\n".join(wizard.plan(args(*extra)))


def test_install_repo_outside_org_is_rejected():
    with pytest.raises(wizard.WizardError, match="not in Giskard-AI"):
        wizard.validate(args("--install-repo", "Other/lib"))


def test_plan_names_every_change():
    steps = "\n".join(wizard.plan(args("--bypass-ruleset", "42")))
    for text in ("release", "svc-release", "RELEASE_APP_PRIVATE_KEY", "Giskard-AI/svc only", "42"):
        assert text in steps


POLICIES = "repos/Giskard-AI/svc/environments/release/deployment-branch-policies"


@pytest.mark.parametrize(
    ("existing", "deleted", "created"),
    [
        pytest.param([], [], True, id="new-environment-gets-the-branch"),
        pytest.param([{"id": 1, "name": "main", "type": "branch"}], [], False,
                     id="branch-already-alone-is-kept"),
        pytest.param([{"id": 1, "name": "main", "type": None}], [], False,
                     id="policy-without-type-is-a-branch"),
        pytest.param(
            [{"id": 1, "name": "main", "type": "branch"},
             {"id": 2, "name": "dev", "type": "branch"},
             {"id": 3, "name": "v*", "type": "tag"}],
            [2, 3], False, id="other-branches-and-tags-are-deleted",
        ),
        pytest.param([{"id": 4, "name": "main", "type": "tag"}], [4], True,
                     id="tag-of-the-same-name-is-replaced"),
    ],
)  # fmt: skip
def test_restrict_to_branch(monkeypatch, existing, deleted, created):
    calls = []

    def gh(*argv, stdin=None):
        calls.append(argv)
        return wizard.json.dumps(existing) if argv[1] == POLICIES else ""

    monkeypatch.setattr(wizard, "_gh", gh)
    wizard.restrict_to_branch("Giskard-AI/svc", "release", "main")
    assert [c[3] for c in calls if c[2] == "DELETE"] == [f"{POLICIES}/{i}" for i in deleted]
    posts = [c for c in calls if c[2] == "POST"]
    assert posts == ([("api", "-X", "POST", POLICIES, "-f", "name=main", "-f", "type=branch")]
                     if created else [])  # fmt: skip


def test_callback_rejects_a_foreign_state():
    with pytest.raises(wizard.WizardError, match="state"):
        wizard.callback_code({"state": ["other"], "code": ["abc"]}, "mine")
