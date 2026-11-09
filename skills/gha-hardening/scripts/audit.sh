#!/usr/bin/env bash
# Read-only audit of one repository for the gha-hardening skill.
#
# Usage: audit.sh OWNER/REPO CLONE_DIR
#   CLONE_DIR is a local clone of OWNER/REPO, on any branch: the audit fetches
#   the default branch and reads it from a temporary worktree, so its counts
#   are the "before" of the pull request.
# Environment: GATE_REF and ZIZMOR_RUNNER, passed to gate-scan.sh.
#
# Prints, section by section: the Gate scan (the exact scan the org ruleset
# runs), Actions settings, secrets and Environments, pushes and publishing,
# privileged triggers, registry logins, the rules on the default branch and on
# tags with their bypass actors, the GitHub Apps that can reach the
# repository, and Renovate and Dependabot. It changes nothing. A section the
# token cannot read says so and moves on.
set -uo pipefail

repo="${1:?usage: audit.sh OWNER/REPO CLONE_DIR}"
clone=$(cd "${2:?usage: audit.sh OWNER/REPO CLONE_DIR}" && pwd)
owner="${repo%%/*}"

section() { printf '\n## %s\n\n' "$1"; }
unreadable() { echo "(not readable with this token: $1)"; }
# Lines of workflow and action files matching an extended regex, as file:line: text.
find_in_workflows() {
  local found
  found=$(cd "${tree}" && grep -rnE "$1" .github/workflows action.y*ml .github/actions 2>/dev/null)
  sed 's/^/  /' <<<"${found:-none}"
}
# Lines of a workflow's `on:` block matching an extended regex, as file:line: text.
find_in_triggers() {
  local found
  found=$(cd "${tree}" && for file in .github/workflows/*.y*ml; do
    [[ -f "${file}" ]] && awk -v re="$1" -v f="${file}" '
      /^.?on.?:/ { in_on = 1; if ($0 ~ re) print f ":" FNR ": " $0; next }
      /^[^ #]/ { in_on = 0 }
      in_on && $0 ~ re { print f ":" FNR ": " $0 }' "${file}"
  done)
  sed 's/^/  /' <<<"${found:-none}"
}

if ! branch=$(gh api "repos/${repo}" --jq .default_branch 2>&1) || [[ -z "${branch}" ]]; then
  echo "Cannot read ${repo} from the GitHub API: ${branch}" >&2
  echo "Fix gh authentication or network access, then run the audit again." >&2
  exit 2
fi
if ! git -C "${clone}" fetch -q origin "+refs/heads/${branch}:refs/remotes/origin/${branch}"; then
  echo "Cannot fetch ${branch} into ${clone}; check its origin remote." >&2
  exit 2
fi
tree=$(mktemp -d)
trap 'git -C "${clone}" worktree remove --force "${tree}" 2>/dev/null; rm -rf "${tree}"' EXIT
git -C "${clone}" worktree add -q --detach "${tree}" "origin/${branch}" || exit 2
rules=$(gh api "repos/${repo}/rules/branches/${branch}?per_page=100" 2>/dev/null || echo "[]")
echo "Audited: ${repo}@${branch} ($(git -C "${tree}" rev-parse --short HEAD))"

section "Gate scan"
bash "$(dirname "$0")/gate-scan.sh" "${repo}" "${tree}" 2>&1 | grep -v ' INFO '
echo "Gate scan exit status: ${PIPESTATUS[0]} (0 pass, 10-14 findings by highest severity, 1 or 2 scan error)"

section "Actions settings"
gh api "repos/${repo}/actions/permissions" --jq '"enabled: \(.enabled), allowed actions: \(.allowed_actions // "n/a")"' 2>/dev/null \
  || unreadable "needs repository admin"
gh api "repos/${repo}/actions/permissions/workflow" \
  --jq '"default GITHUB_TOKEN: \(.default_workflow_permissions), can approve PRs: \(.can_approve_pull_request_reviews)"' 2>/dev/null \
  || unreadable "needs repository admin"

section "Secrets"
referenced=$(cd "${tree}" && grep -rhoE 'secrets\.[A-Za-z0-9_]+' .github action.y*ml 2>/dev/null \
  | sed 's/^secrets\.//' | grep -vx GITHUB_TOKEN | sort -u)
echo "Repository secrets (goal: none; \"unused\" = no workflow on ${branch} reads it):"
if repo_secrets=$(gh api "repos/${repo}/actions/secrets" --jq '.secrets[].name' 2>/dev/null); then
  for name in ${repo_secrets}; do
    grep -qx "${name}" <<<"${referenced}" && echo "  ${name}" || echo "  ${name} (unused)"
  done
else
  unreadable "needs repository admin"
fi
echo "Organization secrets visible to this repository:"
gh api "repos/${repo}/actions/organization-secrets" --jq '.secrets[] | "  \(.name)"' 2>/dev/null || unreadable "needs repository admin"
echo "Environments (secrets of an Environment deployable from any branch are readable from any branch):"
for env in $(gh api "repos/${repo}/environments" --jq '.environments[].name' 2>/dev/null); do
  policy=$(gh api "repos/${repo}/environments/${env}" --jq '
    if .deployment_branch_policy == null then "any branch"
    elif .deployment_branch_policy.protected_branches then "protected branches"
    else "custom branches" end')
  branches=""
  if [[ "${policy}" == "custom branches" ]]; then
    branches=$(gh api "repos/${repo}/environments/${env}/deployment-branch-policies" \
      --jq '[.branch_policies[] | "\(.type // "branch") \(.name)"] | join(", ")' 2>/dev/null)
  fi
  echo "  ${env} (deploys from: ${policy}${branches:+: ${branches}})"
  gh api "repos/${repo}/environments/${env}/secrets" --jq '.secrets[] | "    secret \(.name)"' 2>/dev/null
  gh api "repos/${repo}/environments/${env}/variables" --jq '.variables[] | "    variable \(.name)"' 2>/dev/null
done
echo "Secrets referenced by workflows and actions:"
sed 's/^/  /' <<<"${referenced:-none}"

section "Pushes and publishing"
echo "Pushes and releases (a push to ${branch} with github.token fails once a rule requires pull requests or checks):"
find_in_workflows 'git push|gh release (create|upload|edit)|(npm|yarn|pnpm|uv) publish|twine upload|docker push|gh-action-pypi-publish|action-gh-release'
echo "Files that mint an App token:"
find_in_workflows 'create-github-app-token' 
echo "Workflows triggered by a pushed tag (tag creation must be reserved):"
find_in_triggers '^ +tags(-ignore)?:'

section "Privileged triggers"
echo "pull_request_target, workflow_run, issue_comment (run the reference.md section 13 checklist on each):"
find_in_triggers 'pull_request_target|workflow_run|issue_comment'

section "Registry logins"
echo "Static passwords (move to OIDC, reference.md section 9):"
find_in_workflows 'password:\s*\$\{\{\s*secrets\.|docker login .*(secrets\.|-p |--password)'

section "Rules on ${branch}"
for id in $(jq -r '[.[].ruleset_id] | unique[]' <<<"${rules}"); do
  jq -r --argjson id "${id}" '[.[] | select(.ruleset_id == $id)]
    | "ruleset \($id) (\(.[0].ruleset_source_type) \(.[0].ruleset_source)): \([.[].type] | join(", "))",
      (.[] | select(.type == "required_status_checks") | .parameters.required_status_checks[]
        | "  required check: \(.context)")' <<<"${rules}"
  if [[ $(jq -r --argjson id "${id}" 'first(.[] | select(.ruleset_id == $id)) | .ruleset_source_type' <<<"${rules}") == Repository ]]; then
    gh api "repos/${repo}/rulesets/${id}" \
      --jq '"  bypass: \([.bypass_actors[] | "\(.actor_type)\(if .actor_id then " \(.actor_id)" else "" end) (\(.bypass_mode))"] | join("; ") | if . == "" then "none" else . end)"' 2>/dev/null
  else
    echo "  bypass: needs admin:org; see https://github.com/organizations/${owner}/settings/rules/${id}"
  fi
done
if gh api "repos/${repo}/branches/${branch}/protection" --silent 2>/dev/null; then
  echo "classic branch protection: on"
fi

section "Rules on tags"
tag_rulesets=$(gh api "repos/${repo}/rulesets?includes_parents=true&targets=tag&per_page=100" --jq '.[].id' 2>/dev/null)
for id in ${tag_rulesets}; do
  gh api "repos/${repo}/rulesets/${id}" --jq '"ruleset \(.id) \(.name) (\(.source_type) \(.source), \(.enforcement))",
    "  refs: \(.conditions.ref_name.include | join(", ")); rules: \([.rules[].type] | join(", "))"' 2>/dev/null
done
[[ -n "${tag_rulesets}" ]] || echo "No ruleset protects tags."

section "GitHub Apps that can reach this repository"
# Sensitive: write access that can change code, workflows, settings or secrets.
if installs=$(gh api "orgs/${owner}/installations?per_page=100" 2>/dev/null); then
  jq -r '.installations[] | select(.suspended_at == null)
    | [.permissions | to_entries[]
        | select(.key | IN("contents", "workflows", "actions", "administration", "secrets", "environments"))
        | select(.value != "read") | "\(.key)=\(.value)"] as $sensitive
    | "\(if ($sensitive | length) > 0 and .repository_selection == "all" then "SENSITIVE " else "" end)"
      + "\(.app_slug): \(.repository_selection) repositories"
      + "\(if ($sensitive | length) > 0 then "; " + ($sensitive | join(", ")) else "; no write on code, workflows, settings or secrets" end)"' <<<"${installs}"
  echo "(SENSITIVE: write on code, workflows, settings or secrets of every repository. \"selected\" apps may not cover this repository: https://github.com/organizations/${owner}/settings/installations)"
else
  unreadable "listing app installations needs admin:org; see the Apps section of the repo-baseline dashboard"
fi

section "Renovate and Dependabot"
config=""
for name in renovate.json renovate.json5 .github/renovate.json .github/renovate.json5 .renovaterc .renovaterc.json; do
  [[ -f "${tree}/${name}" ]] && config="${name}" && break
done
if [[ -n "${config}" ]]; then
  echo "${config}:"
  sed 's/^/  /' "${tree}/${config}"
else
  echo "No Renovate config."
fi
last=$(gh pr list -R "${repo}" --author app/renovate --state all --limit 1 --json createdAt --jq '.[0].createdAt // empty' 2>/dev/null)
if [[ -n "${last}" ]]; then
  echo "renovate App covers this repository: yes (last pull request ${last%%T*})"
else
  echo "renovate App covers this repository: no pull request from it yet"
fi
if [[ -f "${tree}/.github/dependabot.yml" || -f "${tree}/.github/dependabot.yaml" ]]; then
  echo "Dependabot version updates: $(grep -hoE 'package-ecosystem:\s*\S+' "${tree}"/.github/dependabot.y*ml | awk '{print $2}' | tr -d '"'"'" | paste -sd, -)"
else
  echo "Dependabot version updates: none"
fi
gh api "repos/${repo}" --jq '"Dependabot security updates: \(.security_and_analysis.dependabot_security_updates.status // "unknown")"' 2>/dev/null
if gh api "repos/${repo}/vulnerability-alerts" --silent 2>/dev/null; then
  echo "Dependabot alerts: enabled"
else
  echo "Dependabot alerts: disabled, or not readable (needs repository admin)"
fi
