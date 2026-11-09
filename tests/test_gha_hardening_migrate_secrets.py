import importlib.util
from pathlib import Path

import pytest

PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "gha-hardening"
    / "scripts"
    / "migrate_secrets.py"
)
spec = importlib.util.spec_from_file_location("migrate_secrets", PATH)
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)

TESTS = wizard.EnvSpec("openai-tests", wizard.Policy("all"), ("OPENAI_API_KEY",))
RELEASE = wizard.EnvSpec(
    "pypi-release", wizard.Policy("tag", "v*"), ("PYPI_TOKEN", "OPENAI_API_KEY")
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("openai-tests:all:OPENAI_API_KEY", TESTS, id="all-branches"),
        pytest.param("pypi-release:tag=v*:PYPI_TOKEN,OPENAI_API_KEY", RELEASE,
                     id="tag-two-secrets"),
        pytest.param(
            "docs-deploy:branch=main:TOKEN",
            wizard.EnvSpec("docs-deploy", wizard.Policy("branch", "main"), ("TOKEN",)),
            id="branch",
        ),
    ],
)  # fmt: skip
def test_parse_env(text, expected):
    assert wizard.parse_env(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("openai-tests:all", "NAME:POLICY:SECRET", id="no-secret"),
        pytest.param("openai-tests:all:openai_key", "UPPER_SNAKE_CASE", id="lowercase-secret"),
        pytest.param("openai-tests:main:KEY", "Policy", id="bare-branch-policy"),
        pytest.param("openai-tests:branch=:KEY", "Policy", id="empty-pattern"),
        pytest.param(":all:KEY", "NAME:POLICY:SECRET", id="no-name"),
    ],
)
def test_bad_env_is_rejected(text, message):
    with pytest.raises(wizard.WizardError, match=message):
        wizard.parse_env(text)


@pytest.mark.parametrize(
    ("repos", "envs", "deletes", "message"),
    [
        pytest.param([], [TESTS], [], "--repo", id="no-repo"),
        pytest.param(["flare"], [TESTS], [], "OWNER/REPO", id="repo-without-owner"),
        pytest.param(["G/flare"], [TESTS, TESTS], [], "once", id="environment-twice"),
        pytest.param(["G/flare"], [TESTS], ["OPENAI_API_KEY"], "both copied and deleted",
                     id="copied-and-deleted"),
    ],
)  # fmt: skip
def test_validation(repos, envs, deletes, message):
    with pytest.raises(wizard.WizardError, match=message):
        wizard.validate(repos, envs, deletes)


@pytest.mark.parametrize(
    ("state", "deletes", "expected"),
    [
        pytest.param(
            wizard.RepoState({"OPENAI_API_KEY"}),
            [],
            [
                "Create Environment openai-tests on G/flare, deployable from every branch.",
                "Write OPENAI_API_KEY to openai-tests, then check it is there.",
                "Delete repository secret OPENAI_API_KEY, once every copy is checked.",
            ],
            id="copy-then-delete",
        ),
        pytest.param(
            wizard.RepoState(set(), {"openai-tests": set()}),
            [],
            [
                "Update Environment openai-tests on G/flare, deployable from every branch.",
                "Write OPENAI_API_KEY to openai-tests (not a repository secret today), "
                "then check it is there.",
            ],
            id="existing-environment-new-secret",
        ),
        pytest.param(
            wizard.RepoState({"OPENAI_API_KEY", "OLD_PAT", "OTHER"}),
            ["OLD_PAT", "GONE"],
            [
                "Create Environment openai-tests on G/flare, deployable from every branch.",
                "Write OPENAI_API_KEY to openai-tests, then check it is there.",
                "Delete repository secret OPENAI_API_KEY, once every copy is checked.",
                "Delete unused repository secret OLD_PAT.",
                "Skip GONE: not a repository secret of G/flare.",
                "Left at repository level, not named above: OTHER.",
            ],
            id="delete-unused-and-report-the-rest",
        ),
    ],
)
def test_migration_plan(state, deletes, expected):
    assert wizard.migration_plan("G/flare", state, [TESTS], deletes) == expected


def test_plan_describes_a_tag_policy():
    steps = wizard.migration_plan("G/flare", wizard.RepoState(), [RELEASE], [])
    assert steps[0] == "Create Environment pypi-release on G/flare, deployable from tag v* only."


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        pytest.param(wizard.Policy("all"), {"deployment_branch_policy": None}, id="all"),
        pytest.param(
            wizard.Policy("branch", "main"),
            {"deployment_branch_policy": {"protected_branches": False,
                                          "custom_branch_policies": True}},
            id="custom",
        ),
    ],
)  # fmt: skip
def test_environment_body(policy, expected):
    assert policy.environment_body() == expected


def test_rotation_finds_every_copy():
    states = {
        "G/flare": wizard.RepoState(set(), {"openai-tests": {"OPENAI_API_KEY"}, "docs": {"X"}}),
        "G/bench": wizard.RepoState(
            {"OPENAI_API_KEY"}, {"release": {"OPENAI_API_KEY"}, "e2e-tests": {"OPENAI_API_KEY"}}
        ),
    }
    assert wizard.rotation_targets("OPENAI_API_KEY", states) == [
        ("G/flare", "openai-tests"),
        ("G/bench", "e2e-tests"),
        ("G/bench", "release"),
    ]


def test_each_value_is_asked_once(monkeypatch):
    prompts = []
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt: prompts.append(prompt) or "v")
    values = wizard.ask_values({"OPENAI_API_KEY": ["openai-tests", "pypi-release"], "PYPI": ["x"]})
    assert values == {"OPENAI_API_KEY": "v", "PYPI": "v"}
    assert prompts == ["  OPENAI_API_KEY (for openai-tests, pypi-release): ", "  PYPI (for x): "]


def test_empty_value_stops(monkeypatch):
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt: "")
    with pytest.raises(wizard.WizardError, match="Empty value"):
        wizard.ask_values({"KEY": ["env"]})


def test_rotate_takes_no_env():
    with pytest.raises(SystemExit):
        wizard.parse_args(["--rotate", "KEY", "--repo", "G/r", "--env", "a:all:KEY"])
