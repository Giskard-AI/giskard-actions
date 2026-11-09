import importlib.util
from pathlib import Path

import pytest

PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "gha-hardening"
    / "scripts"
    / "workflow_contract.py"
)
spec = importlib.util.spec_from_file_location("workflow_contract", PATH)
contract_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract_script)
contract = contract_script.contract
broken_references = contract_script.broken_references

SHA = "de0fac2e4500dabe0009e67214ff5f5447ce83dd"


def job(**fields):
    return {"runs-on": "ubuntu-latest", "steps": [], **fields}


@pytest.mark.parametrize(
    ("doc", "field", "expected"),
    [
        pytest.param({True: "push"}, "triggers", ["push"], id="on-parsed-as-true"),
        pytest.param({"on": ["push", "pull_request"]}, "triggers", ["pull_request", "push"],
                     id="trigger-list"),
        pytest.param({"on": {"push": {"tags": ["v*"]}}}, "triggers", ['push {"tags": ["v*"]}'],
                     id="trigger-filters"),
        pytest.param({"on": {"workflow_dispatch": {"inputs": {"version": {"required": True}}}}},
                     "inputs", ['workflow_dispatch.version: {"required": true}'],
                     id="dispatch-input"),
        pytest.param({"on": {"workflow_call": {"secrets": {"KEY": {"required": True}}}}},
                     "secrets", ['workflow_call declares KEY: {"required": true}'],
                     id="declared-secret"),
        pytest.param({"permissions": {}}, "permissions", ["workflow: none"],
                     id="empty-permissions"),
        pytest.param({"jobs": {"t": job(permissions={"contents": "read"})}}, "permissions",
                     ["t: contents=read"], id="job-permissions"),
        pytest.param({"jobs": {"t": job(environment={"name": "openai-tests",
                                                     "deployment": False})}},
                     "environments", ['t: {"deployment": false, "name": "openai-tests"}'],
                     id="environment-without-deployment"),
        pytest.param({"jobs": {"t": job(steps=[{"env": {"K": "${{ secrets.OPENAI_API_KEY }}"},
                                                "run": "echo ${{ vars.MODEL }}"}])}},
                     "secrets", ["t: OPENAI_API_KEY"], id="secret-read"),
        pytest.param({"env": {"K": "${{ secrets.KEY }}"}, "jobs": {"a": job(), "b": job()}},
                     "secrets", ["a: KEY (workflow env)", "b: KEY (workflow env)"],
                     id="workflow-env-secret-read-by-every-job"),
        pytest.param({"jobs": {"t": job(env={"K": "${{ secrets.OPENAI_API_KEY }}"})}},
                     "secrets", ["t: OPENAI_API_KEY (job env)"], id="job-env-secret"),
        pytest.param({"jobs": {"t": job(steps=[{"run": "echo ${{ vars.MODEL }}"}])}},
                     "variables", ["t: MODEL"], id="variable-read"),
        pytest.param({"env": {"M": "${{ vars.MODEL }}"}, "jobs": {"t": job()}},
                     "variables", ["t: MODEL (workflow env)"], id="workflow-env-variable"),
        pytest.param({"jobs": {"test": job(strategy={"matrix": {"py": ["3.12"]}})}}, "checks",
                     ["test: test (+ matrix values)"], id="matrix-check-name"),
        pytest.param({"jobs": {"build-docs": job(name="Build docs")}}, "checks",
                     ["build-docs: Build docs"], id="named-check"),
        pytest.param({"jobs": {"r": job(steps=[
                         {"run": "git push origin main\ngh release create v1"},
                         {"uses": "pypa/gh-action-pypi-publish@" + SHA}])}},
                     "publishes",
                     ["r: gh release create", "r: git push", "r: pypa/gh-action-pypi-publish"],
                     id="publishing-commands"),
        pytest.param({"runs": {"using": "composite", "steps": [{"uses": "actions/checkout@v4"}]}},
                     "actions", ["(action): actions/checkout@v4"], id="composite-action"),
    ],
)  # fmt: skip
def test_contract_field(doc, field, expected):
    assert contract(doc)[field] == expected


def test_pinned_action_shows_its_version_comment():
    doc = {"jobs": {"t": job(steps=[{"uses": f"actions/checkout@{SHA}"}])}}
    raw = f"      - uses: actions/checkout@{SHA} # v6.0.2\n"
    assert contract(doc, raw)["actions"] == ["t: actions/checkout@de0fac2e4500 (v6.0.2)"]


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        pytest.param(
            {"jobs": {"a": job(steps=[{"id": "v", "run": "x"},
                                      {"run": "echo ${{ steps.v.outputs.version }}"}])}},
            [], id="step-exists",
        ),
        pytest.param(
            {"jobs": {"a": job(steps=[{"run": "echo ${{ steps.v.outputs.version }}"}])}},
            ["a: steps.v.outputs.version, no step has id v"], id="step-removed",
        ),
        pytest.param(
            {"jobs": {"a": job(outputs={"version": "x"}),
                      "b": job(needs="a", steps=[{"run": "echo ${{ needs.a.outputs.tag }}"}])}},
            ["b: needs.a.outputs.tag, a sets no tag"], id="job-output-removed",
        ),
        pytest.param(
            {"jobs": {"b": job(steps=[{"run": "echo ${{ needs.gone.outputs.tag }}"}])}},
            ["b: needs.gone.outputs.tag, no job gone"], id="job-removed",
        ),
        pytest.param(
            {"on": {"workflow_call": {"outputs": {"v": {"value": "${{ jobs.a.outputs.v }}"}}}},
             "jobs": {"a": job()}},
            ["workflow_call.v: jobs.a.outputs.v is not set"], id="workflow-output-unset",
        ),
    ],
)  # fmt: skip
def test_broken_references(doc, expected):
    assert broken_references(doc) == expected


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        pytest.param(None, {}, "#### `w.yml` (new)\n", id="new-file"),
        pytest.param({}, None, "#### `w.yml` (deleted)\n", id="deleted-file"),
        pytest.param({"on": "push"}, {"on": "push"}, "#### `w.yml`\n\nContract unchanged.\n",
                     id="unchanged"),
        pytest.param(
            {"permissions": {"contents": "write"}}, {"permissions": {}},
            "#### `w.yml`\n\n| Contract | Before | After |\n|---|---|---|\n"
            "| permissions | `workflow: contents=write` | `workflow: none` |\n",
            id="changed-row",
        ),
        pytest.param(
            {"env": {"K": "${{ secrets.KEY }}"}, "jobs": {"t": job()}},
            {"jobs": {"t": job(steps=[{"env": {"K": "${{ secrets.KEY }}"}, "run": "x"}])}},
            "#### `w.yml`\n\n| Contract | Before | After |\n|---|---|---|\n"
            "| secrets | `t: KEY (workflow env)` | `t: KEY` |\n",
            id="secret-moved-from-workflow-env-to-a-step",
        ),
    ],
)  # fmt: skip
def test_render(before, after, expected):
    to_contract = lambda doc: None if doc is None else contract(doc)  # noqa: E731
    assert contract_script.render("w.yml", to_contract(before), to_contract(after)) == expected


@pytest.mark.parametrize(
    ("sections", "says_only_changed_lines"),
    [
        pytest.param(["#### `w.yml`\n\nContract unchanged.\n"], True, id="with-a-changed-file"),
        pytest.param([], False, id="no-changed-file"),
    ],
)
def test_report_says_the_table_lists_only_changed_lines(sections, says_only_changed_lines):
    text = contract_script.report(sections, [], [])
    assert ("Only the contract lines that changed are listed" in text) is says_only_changed_lines


def test_new_lint_errors_ignore_moved_lines():
    old = {"filepath": "a.yml", "kind": "expression", "message": "m", "line": 3}
    moved = {**old, "line": 9}
    new = {**old, "message": "other", "line": 1}
    assert contract_script.new_lint_errors([old], [moved, new]) == [new]


def test_only_workflows_and_actions_are_compared():
    pattern = contract_script.WORKFLOW
    assert pattern.match(".github/workflows/ci.yml")
    assert pattern.match("action.yaml")
    assert pattern.match(".github/actions/setup/action.yml")
    assert not pattern.match(".github/dependabot.yml")
