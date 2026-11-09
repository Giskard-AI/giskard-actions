"""Move repository secrets into Secret Environments, or rotate a secret everywhere.

Each --env NAME:POLICY:SECRET[,SECRET] creates or updates the Environment NAME,
restricts it to POLICY (all, branch=<branch> or tag=<pattern>) and writes the
listed secrets to it. Each value is asked once, hidden, then piped to
`gh secret set --env` on every repository and Environment that needs it; it
never touches disk. Once every copy is verified, the repository secrets of the
same names are deleted, as are the unused ones given with --delete.

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
  python3 migrate_secrets.py --repo Giskard-AI/flare \\
    --env openai-tests:all:OPENAI_API_KEY \\
    --env pypi-release:tag=v*:PYPI_TOKEN --delete UNUSED_TOKEN --dry-run
  python3 migrate_secrets.py --rotate OPENAI_API_KEY \\
    --repo Giskard-AI/flare --repo Giskard-AI/safety-bench

Needs gh, logged in with admin rights on each repository.
"""

import argparse
import getpass
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

ENVIRONMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SECRET = re.compile(r"^[A-Z_][A-Z0-9_]*$")
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


@dataclass
class RepoState:
    secrets: set[str] = field(default_factory=set)
    environments: dict[str, set[str]] = field(default_factory=dict)


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


def validate(repos: list[str], envs: list[EnvSpec], deletes: list[str]) -> None:
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


def migration_plan(
    repo: str, state: RepoState, envs: list[EnvSpec], deletes: list[str]
) -> list[str]:
    steps = []
    for env in envs:
        verb = "Update" if env.name in state.environments else "Create"
        steps.append(f"{verb} Environment {env.name} on {repo}, {env.policy.describe()}.")
        for secret in env.secrets:
            source = "" if secret in state.secrets else " (not a repository secret today)"
            steps.append(f"Write {secret} to {env.name}{source}, then check it is there.")
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


def run_migration(args: argparse.Namespace) -> None:
    envs = [parse_env(text) for text in args.env]
    validate(args.repo, envs, args.delete)
    if not envs and not args.delete:
        raise WizardError("Nothing to do: give --env or --delete.")
    states = {repo: read_state(repo) for repo in args.repo}
    print("\nSecret migration. Nothing happens before you press Enter.\n")
    for repo, state in states.items():
        print(f"  {repo}")
        for i, step in enumerate(migration_plan(repo, state, envs, args.delete), 1):
            print(f"    {i}. {step}")
    if args.dry_run:
        print("\nDry run: nothing changed.")
        return
    input("\n  Press Enter to type the values, Ctrl+C to stop.")
    destinations: dict[str, list[str]] = {}
    for env in envs:
        for secret in env.secrets:
            destinations.setdefault(secret, []).append(env.name)
    values = ask_values(destinations)

    for repo, state in states.items():
        _title(repo)
        for env in envs:
            put_environment(repo, env)
            for secret in env.secrets:
                write_secret(repo, env.name, secret, values[secret])
            print(f"  ✓ {env.name}: {env.policy.describe()}, {', '.join(env.secrets)} set.")
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
    print(f"\nRotate {args.rotate}. Nothing happens before you press Enter.\n")
    for repo, env in targets:
        print(f"  Write the new value to {repo}, Environment {env}.")
    for repo, state in states.items():
        if args.rotate in state.secrets:
            print(f"  Not touched: {repo} still has it as a repository secret; migrate it first.")
    if args.dry_run:
        print("\nDry run: nothing changed.")
        return
    input("\n  Press Enter to type the new value, Ctrl+C to stop.")
    value = ask_values({args.rotate: [f"{repo}/{env}" for repo, env in targets]})[args.rotate]
    for repo, env in targets:
        write_secret(repo, env, args.rotate, value)
        print(f"  ✓ {repo}/{env}")
    print("\nDone. Revoke the old value at its provider.")


def read_state(repo: str) -> RepoState:
    def names(*extra: str) -> set[str]:
        out = _gh("secret", "list", "-R", repo, *extra, "--json", "name", "--jq", ".[].name")
        return set(out.split())

    environments = _gh(
        "api", "--paginate", f"repos/{repo}/environments", "--jq", ".environments[].name"
    ).split()
    return RepoState(names(), {env: names("--env", env) for env in environments})


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
    parser.add_argument("--rotate", metavar="NAME", help="rotate this secret everywhere")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.rotate and (args.env or args.delete):
        parser.error("--rotate takes only --repo and --dry-run.")
    return args


if __name__ == "__main__":
    try:
        arguments = parse_args(sys.argv[1:])
        (run_rotation if arguments.rotate else run_migration)(arguments)
    except (WizardError, KeyboardInterrupt) as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        sys.exit(1)
