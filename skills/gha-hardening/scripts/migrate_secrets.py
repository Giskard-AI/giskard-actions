"""Move repository secrets into Secret Environments, or rotate a secret everywhere.

Each --env NAME:POLICY:SECRET[,SECRET] creates or updates the Environment NAME,
restricts it to POLICY (all, branch=<branch> or tag=<pattern>) and writes the
listed secrets to it. Each value is asked once, hidden, then piped to
`gh secret set --env` on every repository and Environment that needs it; it
never touches disk. A multi-line value (a PEM private key) comes from a file
with --from-file NAME=PATH, or from stdin with NAME=-; delete the file once
the plan is done. Once every copy is verified, the repository secrets of the
same names are deleted, as are the unused ones given with --delete.

Each --variables ENV:FROM:VAR[,VAR] copies variables into the Environment ENV
of an --env, from the repository variables (FROM is `repo`) or from another
Environment (FROM is env=<name>). Variables are readable, so nothing is asked;
the source keeps them.

Run it just before merging the pull request whose jobs declare these
Environments: until then, workflows on the default branch still read the
repository secrets, and so do the pull request's jobs (the Environment GitHub
creates on their first run is empty). Rerunning the pull request's checks
proves the migration: the jobs that read the secrets pass, or fail with an
error showing the secret arrived.

With --rotate NAME, it finds every Environment of the given repositories that
holds NAME, asks for the new value once and writes it to each.

Nothing changes before you confirm the plan, and --dry-run only prints it.

Examples:
  python3 migrate_secrets.py --repo OWNER/REPO \\
    --env openai-tests:all:OPENAI_API_KEY \\
    --env pypi-release:tag=v*:PYPI_TOKEN --delete UNUSED_TOKEN --dry-run
  python3 migrate_secrets.py --rotate OPENAI_API_KEY \\
    --repo OWNER/REPO --repo OWNER/OTHER_REPO
  python3 migrate_secrets.py --repo OWNER/DASHBOARD_REPO \\
    --env github-dashboard:branch=main:BASELINE_APP_PRIVATE_KEY \\
    --from-file BASELINE_APP_PRIVATE_KEY=app.private-key.pem \\
    --variables github-dashboard:env=dashboard:BASELINE_APP_CLIENT_ID --dry-run

Needs gh, logged in with admin rights on each repository.
"""

import argparse
import getpass
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENVIRONMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SECRET = re.compile(r"^[A-Z_][A-Z0-9_]*$")
VARIABLE = SECRET
REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class WizardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    kind: str  # all, branch or tag
    pattern: str = ""

    def describe(self) -> str:
        if self.kind == "all":
            return "deployable from every branch"
        return f"deployable from {self.kind} {self.pattern} only"

    def environment_body(self) -> dict:
        if self.kind == "all":
            return {"deployment_branch_policy": None}
        return {
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            }
        }


@dataclass(frozen=True)
class EnvSpec:
    name: str
    policy: Policy
    secrets: tuple[str, ...]


@dataclass(frozen=True)
class VariableCopy:
    env: str
    source: str  # an Environment, or "" for the repository variables
    names: tuple[str, ...]

    def describe_source(self) -> str:
        return f"Environment {self.source}" if self.source else "the repository variables"


@dataclass
class RepoState:
    secrets: set[str] = field(default_factory=set)
    environments: dict[str, set[str]] = field(default_factory=dict)
    variables: set[str] = field(default_factory=set)
    environment_variables: dict[str, set[str]] = field(default_factory=dict)

    def variables_of(self, source: str) -> set[str]:
        return self.environment_variables.get(source, set()) if source else self.variables


def parse_policy(text: str) -> Policy:
    if text == "all":
        return Policy("all")
    kind, _, pattern = text.partition("=")
    if kind not in ("branch", "tag") or not pattern:
        raise WizardError(f"Policy {text!r}: use all, branch=<branch> or tag=<pattern>.")
    return Policy(kind, pattern)


def parse_env(text: str) -> EnvSpec:
    name, policy, names = (text.split(":", 2) + ["", ""])[:3]
    secrets = tuple(s for s in names.split(",") if s)
    if not ENVIRONMENT.match(name) or not secrets:
        raise WizardError(f"--env {text!r}: use NAME:POLICY:SECRET[,SECRET].")
    for secret in secrets:
        if not SECRET.match(secret):
            raise WizardError(f"{secret}: secret names are UPPER_SNAKE_CASE.")
    return EnvSpec(name, parse_policy(policy), secrets)


def parse_variables(text: str) -> VariableCopy:
    env, source, names = (text.split(":", 2) + ["", ""])[:3]
    variables = tuple(v for v in names.split(",") if v)
    kind, _, environment = source.partition("=")
    if (
        not ENVIRONMENT.match(env)
        or not variables
        or not (source == "repo" or (kind == "env" and ENVIRONMENT.match(environment)))
    ):
        raise WizardError(f"--variables {text!r}: use ENV:repo:VAR[,VAR] or ENV:env=<name>:VAR.")
    for variable in variables:
        if not VARIABLE.match(variable):
            raise WizardError(f"{variable}: variable names are UPPER_SNAKE_CASE.")
    return VariableCopy(env, environment, variables)


def read_files(specs: list[str], wanted: set[str]) -> dict[str, tuple[str, str]]:
    """Each --from-file NAME=PATH as {NAME: (value, where it came from)}; PATH - is stdin."""
    values = {}
    for text in specs:
        name, _, path = text.partition("=")
        if not SECRET.match(name) or not path:
            raise WizardError(f"--from-file {text!r}: use NAME=PATH, or NAME=- for stdin.")
        if name not in wanted:
            raise WizardError(f"--from-file {name}: no --env or --rotate names this secret.")
        try:
            value = sys.stdin.read() if path == "-" else Path(path).expanduser().read_text()
        except OSError as error:
            raise WizardError(f"--from-file {name}: {error}") from error
        if not value.strip():
            raise WizardError(f"Empty value for {name}.")
        lines = len(value.strip().splitlines())
        values[name] = (value, f"{'stdin' if path == '-' else path}, {lines} lines")
    return values


def validate(
    repos: list[str],
    envs: list[EnvSpec],
    deletes: list[str],
    copies: tuple[VariableCopy, ...] = (),
) -> None:
    if not repos:
        raise WizardError("Give at least one --repo OWNER/REPO.")
    for repo in repos:
        if not REPO.match(repo):
            raise WizardError(f"{repo}: use OWNER/REPO.")
    names = [env.name for env in envs]
    if len(set(names)) != len(names):
        raise WizardError("Each Environment is given once; list all its secrets in one --env.")
    copied = {secret for env in envs for secret in env.secrets}
    if both := sorted(copied & set(deletes)):
        raise WizardError(f"{', '.join(both)}: both copied and deleted.")
    for copy in copies:
        if copy.env not in names:
            raise WizardError(f"--variables {copy.env}: give its policy and secrets with --env.")
        if copy.source == copy.env:
            raise WizardError(f"--variables {copy.env}: copies an Environment into itself.")


def migration_plan(
    repo: str,
    state: RepoState,
    envs: list[EnvSpec],
    deletes: list[str],
    copies: tuple[VariableCopy, ...] = (),
    files: dict[str, tuple[str, str]] | None = None,
) -> list[str]:
    steps = []
    for env in envs:
        verb = "Update" if env.name in state.environments else "Create"
        steps.append(f"{verb} Environment {env.name} on {repo}, {env.policy.describe()}.")
        for secret in env.secrets:
            source = "" if secret in state.secrets else " (not a repository secret today)"
            if secret in (files or {}):
                source += f" from {files[secret][1]}"
            steps.append(f"Write {secret} to {env.name}{source}, then check it is there.")
        for copy in (c for c in copies if c.env == env.name):
            for name in copy.names:
                if name in state.variables_of(copy.source):
                    steps.append(
                        f"Copy variable {name} from {copy.describe_source()} to {env.name}."
                    )
                else:
                    steps.append(
                        f"Skip variable {name}: not in {copy.describe_source()} of {repo}."
                    )
    copied = sorted({s for env in envs for s in env.secrets} & state.secrets)
    for secret in copied:
        steps.append(f"Delete repository secret {secret}, once every copy is checked.")
    for secret in deletes:
        if secret in state.secrets:
            steps.append(f"Delete unused repository secret {secret}.")
        else:
            steps.append(f"Skip {secret}: not a repository secret of {repo}.")
    left = sorted(state.secrets - set(copied) - set(deletes))
    if left:
        steps.append(f"Left at repository level, not named above: {', '.join(left)}.")
    return steps


def rotation_targets(name: str, states: dict[str, RepoState]) -> list[tuple[str, str]]:
    return [
        (repo, env)
        for repo, state in states.items()
        for env, secrets in sorted(state.environments.items())
        if name in secrets
    ]


def ask_values(destinations: dict[str, list[str]]) -> dict[str, str]:
    """Ask each secret's value once, hidden; `destinations` maps a name to where it goes."""
    values = {}
    for name, where in destinations.items():
        value = getpass.getpass(f"  {name} (for {', '.join(where)}): ")
        if not value:
            raise WizardError(f"Empty value for {name}.")
        values[name] = value
    return values


def confirm(prompt: str) -> None:
    """Wait for Enter on the terminal, even when stdin carried a value."""
    if sys.stdin.isatty():
        input(prompt)
        return
    with open("/dev/tty") as tty:
        print(prompt, end="", flush=True)
        tty.readline()


def run_migration(args: argparse.Namespace) -> None:
    envs = [parse_env(text) for text in args.env]
    copies = tuple(parse_variables(text) for text in args.variables)
    validate(args.repo, envs, args.delete, copies)
    if not envs and not args.delete:
        raise WizardError("Nothing to do: give --env or --delete.")
    files = read_files(args.from_file, {secret for env in envs for secret in env.secrets})
    states = {repo: read_state(repo) for repo in args.repo}
    print("\nSecret migration. Nothing happens before you press Enter.\n")
    for repo, state in states.items():
        print(f"  {repo}")
        plan = migration_plan(repo, state, envs, args.delete, copies, files)
        for i, step in enumerate(plan, 1):
            print(f"    {i}. {step}")
    if args.dry_run:
        print("\nDry run: nothing changed.")
        return
    confirm("\n  Press Enter to go on, Ctrl+C to stop.")
    destinations: dict[str, list[str]] = {}
    for env in envs:
        for secret in env.secrets:
            if secret not in files:
                destinations.setdefault(secret, []).append(env.name)
    values = {name: value for name, (value, _) in files.items()} | ask_values(destinations)

    for repo, state in states.items():
        _title(repo)
        for env in envs:
            put_environment(repo, env)
            for secret in env.secrets:
                write_secret(repo, env.name, secret, values[secret])
            print(f"  ✓ {env.name}: {env.policy.describe()}, {', '.join(env.secrets)} set.")
            for copy in (c for c in copies if c.env == env.name):
                for name in sorted(set(copy.names) & state.variables_of(copy.source)):
                    copy_variable(repo, copy.source, env.name, name)
                    print(f"  ✓ {env.name}: variable {name} copied from {copy.describe_source()}.")
        copied = {s for env in envs for s in env.secrets} & state.secrets
        for secret in sorted(copied | (set(args.delete) & state.secrets)):
            _gh("secret", "delete", secret, "-R", repo)
            print(f"  ✓ Repository secret {secret} deleted.")
    print(
        "\nDone. Rerun the pull request's checks: the jobs that read these secrets pass,"
        " or fail with an error showing the secret arrived."
    )


def run_rotation(args: argparse.Namespace) -> None:
    validate(args.repo, [], [])
    if not SECRET.match(args.rotate):
        raise WizardError(f"{args.rotate}: secret names are UPPER_SNAKE_CASE.")
    states = {repo: read_state(repo) for repo in args.repo}
    targets = rotation_targets(args.rotate, states)
    if not targets:
        raise WizardError(f"No Environment of {', '.join(args.repo)} holds {args.rotate}.")
    files = read_files(args.from_file, {args.rotate})
    source = f" from {files[args.rotate][1]}" if files else ""
    print(f"\nRotate {args.rotate}. Nothing happens before you press Enter.\n")
    for repo, env in targets:
        print(f"  Write the new value{source} to {repo}, Environment {env}.")
    for repo, state in states.items():
        if args.rotate in state.secrets:
            print(f"  Not touched: {repo} still has it as a repository secret; migrate it first.")
    if args.dry_run:
        print("\nDry run: nothing changed.")
        return
    confirm("\n  Press Enter to go on, Ctrl+C to stop.")
    if files:
        value = files[args.rotate][0]
    else:
        value = ask_values({args.rotate: [f"{repo}/{env}" for repo, env in targets]})[args.rotate]
    for repo, env in targets:
        write_secret(repo, env, args.rotate, value)
        print(f"  ✓ {repo}/{env}")
    print("\nDone. Revoke the old value at its provider.")


def read_state(repo: str) -> RepoState:
    def names(*extra: str) -> set[str]:
        out = _gh("secret", "list", "-R", repo, *extra, "--json", "name", "--jq", ".[].name")
        return set(out.split())

    def variables(*extra: str) -> set[str]:
        out = _gh("variable", "list", "-R", repo, *extra, "--json", "name", "--jq", ".[].name")
        return set(out.split())

    environments = _gh(
        "api", "--paginate", f"repos/{repo}/environments", "--jq", ".environments[].name"
    ).split()
    return RepoState(
        names(),
        {env: names("--env", env) for env in environments},
        variables(),
        {env: variables("--env", env) for env in environments},
    )


def put_environment(repo: str, env: EnvSpec) -> None:
    path = f"repos/{repo}/environments/{env.name}"
    _gh("api", "-X", "PUT", path, "--input", "-", stdin=json.dumps(env.policy.environment_body()))
    if env.policy.kind == "all":
        return
    wanted = {"name": env.policy.pattern, "type": env.policy.kind}
    existing = json.loads(
        _gh(
            "api", f"{path}/deployment-branch-policies",
            "--jq", "[.branch_policies[] | {id, name, type}]",
        )
    )  # fmt: skip
    for policy in existing:
        if {"name": policy["name"], "type": policy.get("type", "branch")} != wanted:
            _gh("api", "-X", "DELETE", f"{path}/deployment-branch-policies/{policy['id']}")
    if not any(p["name"] == wanted["name"] and p.get("type") == wanted["type"] for p in existing):
        _gh(
            "api", "-X", "POST", f"{path}/deployment-branch-policies",
            "-f", f"name={wanted['name']}", "-f", f"type={wanted['type']}",
        )  # fmt: skip


def write_secret(repo: str, env: str, name: str, value: str) -> None:
    _gh("secret", "set", name, "-R", repo, "--env", env, stdin=value)
    _gh("api", f"repos/{repo}/environments/{env}/secrets/{name}", "--jq", ".name")


def copy_variable(repo: str, source: str, env: str, name: str) -> None:
    def read(*scope: str) -> str:
        return _gh("variable", "get", name, "-R", repo, *scope).removesuffix("\n")

    value = read("--env", source) if source else read()
    _gh("variable", "set", name, "-R", repo, "--env", env, "--body", value)
    if read("--env", env) != value:
        raise WizardError(f"{name} in {env} differs from {source or 'the repository'}.")


def _gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode:
        raise WizardError(f"gh {' '.join(args[:4])} failed: {result.stderr.strip()}")
    return result.stdout


def _title(text: str) -> None:
    print(f"\n── {text}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", action="append", default=[], help="OWNER/REPO, repeatable")
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="NAME:POLICY:SECRET[,SECRET], repeatable; POLICY is all, branch=<b> or tag=<p>",
    )
    parser.add_argument(
        "--delete", action="append", default=[], help="unused repository secret, repeatable"
    )
    parser.add_argument(
        "--from-file",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="read this secret's value from a file (- for stdin), for a multi-line value "
        "such as a PEM key; repeatable",
    )
    parser.add_argument(
        "--variables",
        action="append",
        default=[],
        metavar="ENV:FROM:VAR[,VAR]",
        help="copy variables into the Environment ENV of an --env; FROM is repo or env=<name>; "
        "repeatable",
    )
    parser.add_argument("--rotate", metavar="NAME", help="rotate this secret everywhere")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.rotate and (args.env or args.delete or args.variables):
        parser.error("--rotate takes only --repo, --from-file and --dry-run.")
    return args


if __name__ == "__main__":
    try:
        arguments = parse_args(sys.argv[1:])
        (run_rotation if arguments.rotate else run_migration)(arguments)
    except (WizardError, KeyboardInterrupt) as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        sys.exit(1)
