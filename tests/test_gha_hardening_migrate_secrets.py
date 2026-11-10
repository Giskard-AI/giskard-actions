import importlib.util
import io
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
        pytest.param(["app"], [TESTS], [], "OWNER/REPO", id="repo-without-owner"),
        pytest.param(["G/app"], [TESTS, TESTS], [], "once", id="environment-twice"),
        pytest.param(["G/app"], [TESTS], ["OPENAI_API_KEY"], "both copied and deleted",
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
                "Create Environment openai-tests on G/app, deployable from every branch.",
                "Write OPENAI_API_KEY to openai-tests, then check it is there.",
                "Delete repository secret OPENAI_API_KEY, once every copy is checked.",
            ],
            id="copy-then-delete",
        ),
        pytest.param(
            wizard.RepoState(set(), {"openai-tests": set()}),
            [],
            [
                "Update Environment openai-tests on G/app, deployable from every branch.",
                "Write OPENAI_API_KEY to openai-tests (not a repository secret today), "
                "then check it is there.",
            ],
            id="existing-environment-new-secret",
        ),
        pytest.param(
            wizard.RepoState({"OPENAI_API_KEY", "OLD_PAT", "OTHER"}),
            ["OLD_PAT", "GONE"],
            [
                "Create Environment openai-tests on G/app, deployable from every branch.",
                "Write OPENAI_API_KEY to openai-tests, then check it is there.",
                "Delete repository secret OPENAI_API_KEY, once every copy is checked.",
                "Delete unused repository secret OLD_PAT.",
                "Skip GONE: not a repository secret of G/app.",
                "Left at repository level, not named above: OTHER.",
            ],
            id="delete-unused-and-report-the-rest",
        ),
    ],
)
def test_migration_plan(state, deletes, expected):
    assert wizard.migration_plan("G/app", state, [TESTS], deletes) == expected


def test_plan_describes_a_tag_policy():
    steps = wizard.migration_plan("G/app", wizard.RepoState(), [RELEASE], [])
    assert steps[0] == "Create Environment pypi-release on G/app, deployable from tag v* only."


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
        "G/app": wizard.RepoState(set(), {"openai-tests": {"OPENAI_API_KEY"}, "docs": {"X"}}),
        "G/bench": wizard.RepoState(
            {"OPENAI_API_KEY"}, {"release": {"OPENAI_API_KEY"}, "e2e-tests": {"OPENAI_API_KEY"}}
        ),
    }
    assert wizard.rotation_targets("OPENAI_API_KEY", states) == [
        ("G/app", "openai-tests"),
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


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(["--env", "a:all:KEY"], id="env"),
        pytest.param(["--variables", "a:repo:VAR"], id="variables"),
    ],
)
def test_rotate_takes_no_env(extra):
    with pytest.raises(SystemExit):
        wizard.parse_args(["--rotate", "KEY", "--repo", "G/r", *extra])


def test_rotate_takes_a_file():
    args = wizard.parse_args(["--rotate", "KEY", "--repo", "G/r", "--from-file", "KEY=k.pem"])
    assert args.from_file == ["KEY=k.pem"]


DASHBOARD = wizard.EnvSpec(
    "github-dashboard", wizard.Policy("branch", "main"), ("BASELINE_APP_PRIVATE_KEY",)
)
CLIENT_ID = wizard.VariableCopy("github-dashboard", "dashboard", ("BASELINE_APP_CLIENT_ID",))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("github-dashboard:env=dashboard:BASELINE_APP_CLIENT_ID", CLIENT_ID,
                     id="from-environment"),
        pytest.param("openai-tests:repo:MODEL,REGION",
                     wizard.VariableCopy("openai-tests", "", ("MODEL", "REGION")),
                     id="from-repository-two-variables"),
    ],
)  # fmt: skip
def test_parse_variables(text, expected):
    assert wizard.parse_variables(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("github-dashboard:env=dashboard", "ENV:repo:VAR", id="no-variable"),
        pytest.param("github-dashboard:dashboard:ID", "ENV:repo:VAR", id="bare-source"),
        pytest.param("github-dashboard:env=:ID", "ENV:repo:VAR", id="empty-source"),
        pytest.param("github-dashboard:repo:client_id", "UPPER_SNAKE_CASE",
                     id="lowercase-variable"),
    ],
)  # fmt: skip
def test_bad_variables_are_rejected(text, message):
    with pytest.raises(wizard.WizardError, match=message):
        wizard.parse_variables(text)


@pytest.mark.parametrize(
    ("copy", "message"),
    [
        pytest.param(wizard.VariableCopy("other", "", ("ID",)), "with --env",
                     id="target-not-an-env"),
        pytest.param(wizard.VariableCopy("github-dashboard", "github-dashboard", ("ID",)),
                     "into itself", id="copy-into-itself"),
    ],
)  # fmt: skip
def test_variable_copy_validation(copy, message):
    with pytest.raises(wizard.WizardError, match=message):
        wizard.validate(["G/r"], [DASHBOARD], [], (copy,))


PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIE\nabcd\n-----END RSA PRIVATE KEY-----\n"


@pytest.mark.parametrize(
    "source",
    [pytest.param("file", id="from-a-file"), pytest.param("stdin", id="from-stdin")],
)
def test_read_files_keeps_a_multi_line_value(tmp_path, monkeypatch, source):
    key = tmp_path / "key.pem"
    key.write_text(PEM)
    monkeypatch.setattr(wizard.sys, "stdin", io.StringIO(PEM))
    path = str(key) if source == "file" else "-"
    values = wizard.read_files([f"KEY={path}"], {"KEY"})
    expected_source = str(key) if source == "file" else "stdin"
    assert values == {"KEY": (PEM, f"{expected_source}, 4 lines")}


@pytest.mark.parametrize(
    ("spec", "content", "message"),
    [
        pytest.param("KEY", None, "NAME=PATH", id="no-path"),
        pytest.param("key={path}", PEM, "NAME=PATH", id="lowercase-name"),
        pytest.param("OTHER={path}", PEM, "no --env or --rotate", id="secret-not-migrated"),
        pytest.param("KEY={path}", "\n", "Empty value", id="empty-file"),
        pytest.param("KEY={path}.missing", None, "No such file", id="missing-file"),
    ],
)
def test_bad_files_are_rejected(tmp_path, spec, content, message):
    key = tmp_path / "key.pem"
    if content is not None:
        key.write_text(content)
    with pytest.raises(wizard.WizardError, match=message):
        wizard.read_files([spec.format(path=key)], {"KEY"})


@pytest.mark.parametrize(
    ("variables", "expected"),
    [
        pytest.param(
            {"dashboard": {"BASELINE_APP_CLIENT_ID"}},
            "Copy variable BASELINE_APP_CLIENT_ID from Environment dashboard to github-dashboard.",
            id="variable-copied",
        ),
        pytest.param(
            {"dashboard": set()},
            "Skip variable BASELINE_APP_CLIENT_ID: not in Environment dashboard of G/r.",
            id="variable-missing",
        ),
    ],
)
def test_environment_rename_plan(variables, expected):
    state = wizard.RepoState(set(), {"dashboard": {"BASELINE_APP_PRIVATE_KEY"}}, set(), variables)
    files = {"BASELINE_APP_PRIVATE_KEY": (PEM, "key.pem, 4 lines")}
    assert wizard.migration_plan("G/r", state, [DASHBOARD], [], (CLIENT_ID,), files) == [
        "Create Environment github-dashboard on G/r, deployable from branch main only.",
        "Write BASELINE_APP_PRIVATE_KEY to github-dashboard (not a repository secret today) "
        "from key.pem, 4 lines, then check it is there.",
        expected,
    ]
