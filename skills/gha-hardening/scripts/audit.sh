#!/usr/bin/env bash
# Read-only audit of one repository for the gha-hardening skill.
#
# Usage: audit.sh OWNER/REPO CLONE_DIR
#   CLONE_DIR is a local clone of OWNER/REPO, on the branch to audit.
# Environment: GATE_REF and ZIZMOR_RUNNER, passed to gate-scan.sh.
#
# Prints, section by section: the Gate scan (the exact scan the org ruleset
# runs), Actions settings, secrets and Environments, the rules on the default
# branch and their bypass actors, the GitHub Apps that can reach the
# repository, and the Renovate config. It changes nothing. A section the token
# cannot read says so and moves on.
set -uo pipefail

repo="${1:?usage: audit.sh OWNER/REPO CLONE_DIR}"
clone=$(cd "${2:?usage: audit.sh OWNER/REPO CLONE_DIR}" && pwd)
owner="${repo%%/*}"

section() { printf '\n## %s\n\n' "$1"; }
unreadable() { echo "(not readable with this token: $1)"; }

branch=$(gh api "repos/${repo}" --jq .default_branch)
rules=$(gh api "repos/${repo}/rules/branches/${branch}?per_page=100" 2>/dev/null || echo "[]")

section "Gate scan"
bash "$(dirname "$0")/gate-scan.sh" "${repo}" "${clone}" 2>&1 | grep -v ' INFO '
echo "Gate scan exit status: ${PIPESTATUS[0]} (0 pass, 10-14 findings by highest severity, 1 or 2 scan error)"

section "Actions settings"
gh api "repos/${repo}/actions/permissions" --jq '"enabled: \(.enabled), allowed actions: \(.allowed_actions // "n/a")"' 2>/dev/null \
  || unreadable "needs repository admin"
gh api "repos/${repo}/actions/permissions/workflow" \
  --jq '"default GITHUB_TOKEN: \(.default_workflow_permissions), can approve PRs: \(.can_approve_pull_request_reviews)"' 2>/dev/null \
  || unreadable "needs repository admin"

section "Secrets"
echo "Repository secrets (goal: none):"
gh api "repos/${repo}/actions/secrets" --jq '.secrets[] | "  \(.name)"' 2>/dev/null || unreadable "needs repository admin"
echo "Organization secrets visible to this repository:"
gh api "repos/${repo}/actions/organization-secrets" --jq '.secrets[] | "  \(.name)"' 2>/dev/null || unreadable "needs repository admin"
echo "Environments:"
for env in $(gh api "repos/${repo}/environments" --jq '.environments[].name' 2>/dev/null); do
  policy=$(gh api "repos/${repo}/environments/${env}" --jq '
    if .deployment_branch_policy == null then "any branch"
    elif .deployment_branch_policy.protected_branches then "protected branches"
    else "custom branches" end')
  branches=$(gh api "repos/${repo}/environments/${env}/deployment-branch-policies" \
    --jq '[.branch_policies[].name] | join(", ")' 2>/dev/null)
  echo "  ${env} (deploys from: ${policy}${branches:+: ${branches}})"
  gh api "repos/${repo}/environments/${env}/secrets" --jq '.secrets[] | "    secret \(.name)"' 2>/dev/null
  gh api "repos/${repo}/environments/${env}/variables" --jq '.variables[] | "    variable \(.name)"' 2>/dev/null
done
echo "Secrets referenced by workflows and actions:"
(cd "${clone}" && grep -rhoE 'secrets\.[A-Za-z0-9_]+' .github action.y*ml 2>/dev/null | sort -u | sed 's/^/  /')

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

section "Renovate"
config=""
for name in renovate.json renovate.json5 .github/renovate.json .github/renovate.json5 .renovaterc .renovaterc.json; do
  [[ -f "${clone}/${name}" ]] && config="${name}" && break
done
if [[ -n "${config}" ]]; then
  echo "${config}:"
  sed 's/^/  /' "${clone}/${config}"
else
  echo "No Renovate config."
fi
