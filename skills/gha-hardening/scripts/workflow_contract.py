# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Show what each changed workflow takes and gives, before and after, and lint both sides.

For every workflow or action file that differs between --base and the working
tree, it prints a markdown table of the Workflow Contract lines that changed:
triggers, inputs, outputs, secrets and variables read (each job reads those
of the workflow-level `env:`, marked so a move into a step shows), permissions,
Environments, check names, actions used, step output references and
publishing commands. It also flags references the new version breaks
(`steps.<id>.outputs` of a step that no longer exists, `needs.<job>.outputs`
the job no longer sets), and runs actionlint on both sides to report only the
errors the branch adds.

Usage:
  uv run workflow_contract.py <clone> --base origin/main

Exit status: 0 nothing broken, 1 broken references or new actionlint errors,
2 the comparison could not run (git, Docker, an error of this script).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path

ACTIONLINT_IMAGE = (
    "rhysd/actionlint:1.7.12"
    "@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667"
)
WORKFLOW = re.compile(r"^(\.github/workflows/[^/]+|(.+/)?action)\.ya?ml$")
STEP_OUTPUT = re.compile(r"steps\.([\w-]+)\.outputs\.([\w-]+)")
NEEDS_OUTPUT = re.compile(r"needs\.([\w-]+)\.outputs\.([\w-]+)")
JOB_OUTPUT = re.compile(r"jobs\.([\w-]+)\.outputs\.([\w-]+)")
SECRET = re.compile(r"secrets\.([A-Za-z_][\w]*)")
VARIABLE = re.compile(r"vars\.([A-Za-z_][\w]*)")
USES_COMMENT = re.compile(r"uses:\s*['\"]?([^\s'\"#]+)['\"]?\s*#\s*(\S+)")
PUBLISH = re.compile(
    r"git push|gh release (?:create|upload|edit|delete)|gh api [^\n]*(?:-X|--method) "
    r"(?:POST|PUT|PATCH|DELETE)|(?:npm|yarn|pnpm) publish|uv publish|twine upload|"
    r"docker (?:buildx build[^\n]*--push|push)"
)
PUBLISH_ACTIONS = (
    "pypa/gh-action-pypi-publish",
    "docker/build-push-action",
    "softprops/action-gh-release",
    "actions/deploy-pages",
    "peaceiris/actions-gh-pages",
)
FIELDS = (
    "triggers",
    "inputs",
    "outputs",
    "secrets",
    "variables",
    "permissions",
    "environments",
    "checks",
    "actions",
    "step outputs",
    "publishes",
)


class ContractError(RuntimeError):
    pass


def _text(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _permissions(owner: str, value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        if not value:
            return [f"{owner}: none"]
        return [f"{owner}: {k}={v}" for k, v in sorted(value.items())]
    return [f"{owner}: {value}"]


def _uses(value: str, comments: dict[str, str]) -> str:
    action, _, ref = value.partition("@")
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        ref = ref[:12] + (f" ({comments[value]})" if value in comments else "")
    return f"{action}@{ref}" if ref else action


def _publishes(owner: str, steps: list) -> list[str]:
    found = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        found.update(f"{owner}: {m}" for m in PUBLISH.findall(str(step.get("run", ""))))
        uses = str(step.get("uses", ""))
        if uses.startswith(PUBLISH_ACTIONS):
            found.add(f"{owner}: {uses.split('@')[0]}")
    return sorted(found)


def contract(doc: dict, raw: str = "") -> dict[str, list[str]]:
    """The Workflow Contract of a parsed workflow or action, as sorted lines per field."""
    comments = dict(USES_COMMENT.findall(raw))
    lines: dict[str, list[str]] = {field: [] for field in FIELDS}
    # PyYAML reads the `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = dict.fromkeys(on)
    for event, config in (on or {}).items():
        # `schedule:` takes a list of crons; other events a mapping, or nothing.
        if config is not None and not isinstance(config, dict):
            lines["triggers"].append(f"{event} {_text(config)}")
            continue
        filters = {
            k: v for k, v in (config or {}).items() if k not in ("inputs", "outputs", "secrets")
        }
        lines["triggers"].append(f"{event}" + (f" {_text(filters)}" if filters else ""))
        for name, spec in ((config or {}).get("inputs") or {}).items():
            lines["inputs"].append(f"{event}.{name}: {_text(spec)}")
        for name, spec in ((config or {}).get("outputs") or {}).items():
            lines["outputs"].append(f"{event}.{name} = {_text((spec or {}).get('value'))}")
        for name, spec in ((config or {}).get("secrets") or {}).items():
            lines["secrets"].append(f"{event} declares {name}: {_text(spec)}")
    for name, spec in (doc.get("inputs") or {}).items():
        lines["inputs"].append(f"action.{name}: {_text(spec)}")
    for name, spec in (doc.get("outputs") or {}).items():
        lines["outputs"].append(f"action.{name} = {_text((spec or {}).get('value'))}")
    lines["permissions"] += _permissions("workflow", doc.get("permissions"))

    jobs = dict(doc.get("jobs") or {})
    if "runs" in doc:
        jobs["(action)"] = {"steps": (doc["runs"] or {}).get("steps") or []}
    workflow_env = _text(doc.get("env"))
    for key, job in jobs.items():
        job = job or {}
        text = _text(job)
        # Every job reads the workflow-level `env:`; the suffix shows a move into a step.
        scopes = {
            "": _text({k: v for k, v in job.items() if k != "env"}),
            " (job env)": _text(job.get("env")),
            " (workflow env)": workflow_env,
        }
        for suffix, scoped in scopes.items():
            lines["secrets"] += [f"{key}: {s}{suffix}" for s in sorted(set(SECRET.findall(scoped)))]
            lines["variables"] += [
                f"{key}: {v}{suffix}" for v in sorted(set(VARIABLE.findall(scoped)))
            ]
        lines["permissions"] += _permissions(key, job.get("permissions"))
        lines["outputs"] += [
            f"{key}.{n} = {_text(v)}" for n, v in (job.get("outputs") or {}).items()
        ]
        env = job.get("environment")
        if env is not None:
            lines["environments"].append(f"{key}: {_text(env)}")
        if key != "(action)":
            name = job.get("name", key)
            matrix = "matrix" in (job.get("strategy") or {}) and "${{" not in str(name)
            lines["checks"].append(f"{key}: {name}" + (" (+ matrix values)" if matrix else ""))
        if job.get("uses"):
            lines["actions"].append(f"{key}: {_uses(job['uses'], comments)}")
        steps = job.get("steps") or []
        lines["actions"] += [
            f"{key}: {_uses(str(s['uses']), comments)}"
            for s in steps
            if isinstance(s, dict) and s.get("uses")
        ]
        lines["step outputs"] += [
            f"{key}: steps.{s}.outputs.{o}" for s, o in sorted(set(STEP_OUTPUT.findall(text)))
        ]
        lines["publishes"] += _publishes(key, steps)
    return {field: sorted(set(values)) for field, values in lines.items()}


def broken_references(doc: dict) -> list[str]:
    """References to step ids, job outputs or jobs that this version does not define."""
    problems = []
    jobs = dict(doc.get("jobs") or {})
    if "runs" in doc:
        jobs["(action)"] = {"steps": (doc["runs"] or {}).get("steps") or []}
    outputs = {key: set((job or {}).get("outputs") or {}) for key, job in jobs.items()}
    for key, job in jobs.items():
        job = job or {}
        ids = {s.get("id") for s in job.get("steps") or [] if isinstance(s, dict)}
        text = _text(job)
        for step, output in sorted(set(STEP_OUTPUT.findall(text))):
            if step not in ids:
                problems.append(f"{key}: steps.{step}.outputs.{output}, no step has id {step}")
        for need, output in sorted(set(NEEDS_OUTPUT.findall(text))):
            if need not in jobs:
                problems.append(f"{key}: needs.{need}.outputs.{output}, no job {need}")
            elif output not in outputs[need]:
                problems.append(f"{key}: needs.{need}.outputs.{output}, {need} sets no {output}")
    on = doc.get("on", doc.get(True))
    calls = (on or {}).get("workflow_call") if isinstance(on, dict) else None
    for name, spec in ((calls or {}).get("outputs") or {}).items():
        for job, output in JOB_OUTPUT.findall(str((spec or {}).get("value", ""))):
            if output not in outputs.get(job, set()):
                problems.append(f"workflow_call.{name}: jobs.{job}.outputs.{output} is not set")
    return problems


def _cell(items: list[str]) -> str:
    return "<br>".join("`" + item.replace("|", "\\|") + "`" for item in items) or "-"


def render(path: str, before: dict | None, after: dict | None) -> str:
    title = f"#### `{path}`"
    if before is None:
        return f"{title} (new)\n"
    if after is None:
        return f"{title} (deleted)\n"
    rows = []
    for field in FIELDS:
        removed = [x for x in before[field] if x not in after[field]]
        added = [x for x in after[field] if x not in before[field]]
        if removed or added:
            rows.append(f"| {field} | {_cell(removed)} | {_cell(added)} |")
    if not rows:
        return f"{title}\n\nContract unchanged.\n"
    return "\n".join([title, "", "| Contract | Before | After |", "|---|---|---|", *rows, ""])


def report(sections: list[str], broken: list[str], lint_new: list[dict]) -> str:
    lines = ["### Workflow Contract", ""]
    if sections:
        lines += [
            "Only the contract lines that changed are listed: a secret, permission or action "
            "the branch keeps as it was does not appear. `(workflow env)` and `(job env)` mark "
            "a secret or variable read in that `env:`, not in a step.",
            "",
            "\n".join(sections),
        ]
    else:
        lines += ["No workflow or action file changed.", ""]
    lines += ["### Checks", ""]
    lines += [f"- Broken reference: {problem}" for problem in broken]
    lines += [
        f"- New actionlint error: {e['filepath']}:{e['line']}: {e['message']}" for e in lint_new
    ]
    if not broken and not lint_new:
        lines.append("- No broken reference; actionlint reports nothing the base does not.")
    return "\n".join(lines) + "\n"


def new_lint_errors(before: list[dict], after: list[dict]) -> list[dict]:
    """actionlint errors of `after` not already in `before`, ignoring line numbers."""

    def key(error: dict) -> tuple:
        return error["filepath"], error["kind"], error["message"]

    seen = Counter(key(e) for e in before)
    new = []
    for error in after:
        if seen[key(error)]:
            seen[key(error)] -= 1
        else:
            new.append(error)
    return new


def _git(clone: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)
    if result.returncode:
        raise ContractError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def changed_files(clone: Path, base: str) -> list[str]:
    tracked = _git(clone, "diff", "--name-only", base, "--").split()
    untracked = _git(clone, "ls-files", "--others", "--exclude-standard").split()
    return sorted(p for p in set(tracked + untracked) if WORKFLOW.match(p))


def actionlint(directory: Path) -> list[dict]:
    files = sorted(
        str(p.relative_to(directory)) for p in (directory / ".github" / "workflows").glob("*.y*ml")
    )
    if not files:
        return []
    if shutil.which("docker"):
        command = ["docker", "run", "--rm", "--network=none",
                   "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{directory}:/repo:ro",
                   "-w", "/repo", ACTIONLINT_IMAGE, "-format", "{{json .}}", *files]  # fmt: skip
    elif shutil.which("actionlint"):
        command = ["actionlint", "-format", "{{json .}}", *files]
    else:
        raise ContractError("actionlint did not run: install Docker or actionlint.")
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
    if result.returncode not in (0, 1) or not result.stdout.strip():
        raise ContractError(f"actionlint did not run: {result.stderr.strip()}")
    return json.loads(result.stdout) or []


def main(argv: list[str]) -> int:
    import yaml

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("clone", type=Path)
    parser.add_argument("--base", required=True, help="git ref of the default branch")
    args = parser.parse_args(argv)
    clone = args.clone.resolve()

    def load(raw: str | None) -> dict | None:
        return None if raw is None else (yaml.safe_load(raw) or {})

    broken, sections = [], []
    for path in changed_files(clone, args.base):
        try:
            old = _git(clone, "show", f"{args.base}:{path}")
        except ContractError:
            old = None
        file = clone / path
        new = file.read_text() if file.exists() else None
        before, after = load(old), load(new)
        sections.append(
            render(path, before and contract(before, old), after and contract(after, new))
        )
        if after is not None:
            broken += [f"{path}: {p}" for p in broken_references(after)]

    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(
            ["git", "-C", str(clone), "archive", args.base, ".github"], capture_output=True
        )
        if archive.returncode == 0:
            subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout, check=True)
        lint_new = new_lint_errors(actionlint(Path(tmp)), actionlint(clone))

    print(report(sections, broken, lint_new), end="")
    return 1 if broken or lint_new else 0


def run(argv: list[str]) -> int:
    """`main`, with every error of the script itself as status 2, never 1."""
    try:
        return main(argv)
    except ContractError as error:
        print(f"Stopped: {error}", file=sys.stderr)
    except Exception:
        traceback.print_exc()
        print(
            "Stopped: the comparison could not run; this is not a contract failure.",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
