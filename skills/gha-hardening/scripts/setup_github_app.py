"""Create a dedicated GitHub App for one job, and store its key in an Environment.

Replaces a personal access token, a deploy key with write access or reaching
several repositories, or an over-broad App. A read-only deploy key that clones
one repository stays a deploy key (reference.md section 8). The Environment
deploys from one branch only, so a pull request job that needs the App's key
is a decision for the pull request. The App is created through GitHub's
manifest flow: GitHub returns the private key once, the wizard keeps it in
memory and pipes it into an Environment secret, never to disk.

Every value is an option, so the command a skill run proposes can be pasted
as-is; a missing value is asked for. Nothing changes before you confirm the
plan, and --dry-run only prints it.

Example:
  python3 setup_github_app.py --org Giskard-AI --name myrepo-release \\
    --repo Giskard-AI/myrepo --permission contents=write \\
    --environment github-release --branch main \\
    --client-id-variable RELEASE_APP_CLIENT_ID --private-key-secret RELEASE_APP_PRIVATE_KEY \\
    --bypass-ruleset 123456 --dry-run

Needs gh, logged in as an org owner (creating an org App, installing it,
editing rulesets) with repository admin on the target repository.
"""

import argparse
import html
import json
import re
import secrets
import shutil
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

LEVELS = {"read", "write", "admin"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,33}$")
VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
CODE = re.compile(r"^[A-Za-z0-9_-]+$")
CALLBACK_TIMEOUT = 300


class WizardError(RuntimeError):
    pass


def parse_permissions(values: list[str]) -> dict[str, str]:
    permissions = {"metadata": "read"}
    for value in values:
        key, _, level = value.partition("=")
        if not key or level not in LEVELS:
            raise WizardError(f"Permission {value!r}: use name=read, name=write or name=admin.")
        permissions[key] = level
    return permissions


def validate(args: argparse.Namespace) -> None:
    if not NAME.match(args.name):
        raise WizardError("App name: letters, digits and dashes, 34 characters at most.")
    for value in (args.client_id_variable, args.private_key_secret):
        if not VARIABLE.match(value):
            raise WizardError(f"{value}: use UPPER_SNAKE_CASE.")
    for repo in (args.repo, *args.install_repo):
        if repo.split("/")[0] != args.org:
            raise WizardError(f"{repo} is not in {args.org}.")


def build_manifest(args: argparse.Namespace, redirect_url: str) -> dict:
    return {
        "name": args.name,
        "url": f"https://github.com/{args.repo}",
        "description": args.description or f"Automation for {args.repo}.",
        "public": False,
        "redirect_url": redirect_url,
        "hook_attributes": {"url": "https://example.com/unused", "active": False},
        "default_permissions": parse_permissions(args.permission),
        "default_events": [],
    }


def with_bypass(ruleset: dict, app_id: int, mode: str) -> dict:
    """The ruleset update body, with the App added once as a bypass actor."""
    actors = [
        actor
        for actor in ruleset.get("bypass_actors", [])
        if not (actor.get("actor_type") == "Integration" and actor.get("actor_id") == app_id)
    ]
    actors.append({"actor_id": app_id, "actor_type": "Integration", "bypass_mode": mode})
    keep = ("name", "target", "enforcement", "conditions", "rules")
    return {**{k: ruleset[k] for k in keep if k in ruleset}, "bypass_actors": actors}


def callback_code(query: dict[str, list[str]], state: str) -> str:
    if query.get("state", [""])[0] != state:
        raise WizardError("GitHub callback state does not match; start the wizard again.")
    code = query.get("code", [""])[0]
    if not CODE.match(code):
        raise WizardError("GitHub callback did not return a valid code.")
    return code


def installed(args: argparse.Namespace) -> list[str]:
    """--install-repo when given (e.g. another repository the job clones), else --repo."""
    return args.install_repo or [args.repo]


def plan(args: argparse.Namespace) -> list[str]:
    permissions = ", ".join(f"{k}: {v}" for k, v in parse_permissions(args.permission).items())
    steps = [
        f"Create or update the Environment {args.environment} on {args.repo}, "
        f"deployable from branch {args.branch} only.",
        f"Create the private App {args.name} in {args.org} ({permissions}). No webhook, no events.",
        f"Store its client ID as variable {args.client_id_variable} and its private key as "
        f"secret {args.private_key_secret} in Environment {args.environment}. "
        "The key never touches disk.",
        f"Install {args.name} on {', '.join(installed(args))} only (you pick them on the install "
        "page).",
    ]
    if args.bypass_ruleset:
        steps.append(
            f"Add {args.name} to the bypass actors of ruleset {args.bypass_ruleset} on "
            f"{args.repo}, mode {args.bypass_mode}."
        )
    return [f"{i}. {step}" for i, step in enumerate(steps, 1)]


def run(args: argparse.Namespace) -> None:
    validate(args)
    print(f"\nGitHub App setup: {args.name}\n")
    print("This wizard does the following. Nothing happens before you press Enter.\n")
    for step in plan(args):
        print(f"  {step}")
    if args.dry_run:
        print("\nDry run. Manifest that would be sent to GitHub:\n")
        print(json.dumps(build_manifest(args, "http://127.0.0.1:<port>/callback"), indent=2))
        return
    _gh("auth", "status")
    input("\n  Press Enter to start, Ctrl+C to stop.")

    _title(f"Environment {args.environment}")
    env_path = f"repos/{args.repo}/environments/{args.environment}"
    _gh(
        "api", "-X", "PUT", env_path,
        "-F", "deployment_branch_policy[protected_branches]=false",
        "-F", "deployment_branch_policy[custom_branch_policies]=true",
    )  # fmt: skip
    names = json.loads(
        _gh("api", f"{env_path}/deployment-branch-policies", "--jq", "[.branch_policies[].name]")
    )
    if args.branch not in names:
        _gh(
            "api", "-X", "POST", f"{env_path}/deployment-branch-policies",
            "-f", f"name={args.branch}", "-f", "type=branch",
        )  # fmt: skip
    print(f"  ✓ {args.environment} deploys from {args.branch} only.")

    _title(f"Create {args.name}")
    print("  Your browser opens a local page that sends the App manifest to GitHub.")
    print(f"  On GitHub, check the name and click 'Create GitHub App for {args.org}'.")
    input("  Press Enter to open the browser.")
    code = _manifest_code(args)
    app = json.loads(_gh("api", "-X", "POST", f"app-manifests/{code}/conversions"))
    print(f"  ✓ Created {app['slug']} (client ID {app['client_id']}), key held in memory.")

    _title("Store the credentials")
    _gh(
        "variable", "set", args.client_id_variable, "-R", args.repo,
        "--env", args.environment, "--body", app["client_id"],
    )  # fmt: skip
    _gh(
        "secret", "set", args.private_key_secret, "-R", args.repo, "--env", args.environment,
        stdin=app.pop("pem"),
    )  # fmt: skip
    print(f"  ✓ {args.client_id_variable} and {args.private_key_secret} set.")

    _title(f"Install {args.name}")
    print(f"  Pick 'Only select repositories', select {', '.join(installed(args))}, click Install.")
    _open(f"{app['html_url']}/installations/new")
    input("  Press Enter once the App is installed.")

    if args.bypass_ruleset:
        _title(f"Ruleset {args.bypass_ruleset}")
        path = f"repos/{args.repo}/rulesets/{args.bypass_ruleset}"
        body = with_bypass(json.loads(_gh("api", path)), app["id"], args.bypass_mode)
        _gh("api", "-X", "PUT", path, "--input", "-", stdin=json.dumps(body))
        print(f"  ✓ {args.name} bypasses ruleset {args.bypass_ruleset} ({args.bypass_mode}).")

    print("\nDone. In the workflow, mint a token with actions/create-github-app-token:")
    print(f"  client-id: ${{{{ vars.{args.client_id_variable} }}}}")
    print(f"  private-key: ${{{{ secrets.{args.private_key_secret} }}}}")
    print(
        f"  on a job with environment: {args.environment}; downscope it with permission-* inputs."
    )


def _manifest_code(args: argparse.Namespace) -> str:
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/start":
                redirect = f"http://127.0.0.1:{self.server.server_port}/callback"
                manifest = html.escape(json.dumps(build_manifest(args, redirect)))
                action = (
                    f"https://github.com/organizations/{args.org}/settings/apps/new?state={state}"
                )
                self._reply(
                    f'<form id="f" method="post" action="{action}">'
                    f'<input type="hidden" name="manifest" value="{manifest}">'
                    "<button>Continue to GitHub</button></form>"
                    '<script>document.getElementById("f").submit()</script>'
                )
            elif url.path == "/callback":
                try:
                    result["code"] = callback_code(parse_qs(url.query), state)
                    self._reply("App created. Close this tab and go back to the terminal.")
                except WizardError as error:
                    result["error"] = str(error)
                    self._reply(html.escape(str(error)))
            else:
                self.send_error(404)

        def _reply(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<!doctype html><body>{body}</body>".encode())

        def log_message(self, *args) -> None:
            pass

    class Server(HTTPServer):
        timeout = CALLBACK_TIMEOUT

        def handle_timeout(self) -> None:
            result["error"] = "No answer from GitHub within 5 minutes."

    with Server(("127.0.0.1", 0), Handler) as server:
        _open(f"http://127.0.0.1:{server.server_port}/start")
        while not result:
            server.handle_request()
    if "error" in result:
        raise WizardError(result["error"])
    return result["code"]


def _open(url: str) -> None:
    print(f"  ↗ {url}")
    for command in ("wslview", "explorer.exe", "xdg-open", "open"):
        if shutil.which(command):
            subprocess.Popen([command, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def _gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode:
        raise WizardError(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def _title(text: str) -> None:
    print(f"\n── {text}")


def _ask(value: str | None, prompt: str) -> str:
    return value or input(f"  {prompt}: ").strip()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org")
    parser.add_argument("--name", help="App name, unique on GitHub, e.g. <repo>-release")
    parser.add_argument("--repo", help="OWNER/REPO whose workflow uses the App")
    parser.add_argument(
        "--install-repo",
        action="append",
        default=[],
        help="OWNER/REPO the App is installed on, repeatable (default: --repo); "
        "e.g. the private repository a job clones, replacing a deploy key",
    )
    parser.add_argument(
        "--permission",
        action="append",
        default=[],
        help="name=read|write|admin, repeatable (metadata=read is always included)",
    )
    parser.add_argument("--environment", help="Environment that holds the credentials")
    parser.add_argument("--branch", help="only branch allowed to deploy to the Environment")
    parser.add_argument("--client-id-variable", help="e.g. RELEASE_APP_CLIENT_ID")
    parser.add_argument("--private-key-secret", help="e.g. RELEASE_APP_PRIVATE_KEY")
    parser.add_argument("--bypass-ruleset", type=int, help="repository ruleset ID to bypass")
    parser.add_argument("--bypass-mode", choices=["always", "pull_request"], default="always")
    parser.add_argument("--description")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.org = _ask(args.org, "Organization")
    args.name = _ask(args.name, "App name")
    args.repo = _ask(args.repo, "Repository (OWNER/REPO)")
    if not args.permission:
        args.permission = _ask(
            None, "Permissions (e.g. contents=write pull_requests=write)"
        ).split()
    args.environment = _ask(args.environment, "Environment")
    args.branch = _ask(args.branch, "Deployment branch")
    args.client_id_variable = _ask(args.client_id_variable, "Client ID variable")
    args.private_key_secret = _ask(args.private_key_secret, "Private key secret")
    return args


if __name__ == "__main__":
    try:
        run(parse_args(sys.argv[1:]))
    except (WizardError, KeyboardInterrupt) as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        sys.exit(1)
