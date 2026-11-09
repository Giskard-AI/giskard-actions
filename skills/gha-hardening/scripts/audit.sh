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
echo "Gate scan exit status: ${PIPESTATUS[0]}"

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
jq -r 'group_by(.ruleset_id)[] | "ruleset \(.[0].ruleset_id) (\(.[0].ruleset_source)): \([.[].type] | join(", "))"' <<<"${rules}"
jq -r '.[] | select(.type == "required_status_checks") | .parameters.required_status_checks[] | "  required check: \(.context)"' <<<"${rules}"
for id in $(jq -r '[.[] | select(.ruleset_source_type == "Repository") | .ruleset_id] | unique[]' <<<"${rules}"); do
  gh api "repos/${repo}/rulesets/${id}" \
    --jq '"ruleset \(.id) bypass: \([.bypass_actors[] | "\(.actor_type)\(if .actor_id then " \(.actor_id)" else "" end) (\(.bypass_mode))"] | join("; ") | if . == "" then "none" else . end)"' 2>/dev/null
done
echo "(Organization ruleset bypass actors need admin:org; see the repo-baseline dashboard.)"

section "GitHub Apps that can reach this repository"
if installs=$(gh api "orgs/${owner}/installations?per_page=100" 2>/dev/null); then
  jq -r '.installations[] | select(.suspended_at == null)
    | "\(.app_slug): \(.repository_selection) repositories; " +
      ([.permissions | to_entries[] | select(.key | IN("contents", "workflows", "actions", "administration", "secrets", "pull_requests"))
        | "\(.key)=\(.value)"] | join(", "))' <<<"${installs}"
  echo "(\"selected\" apps may not include this repository; check each one in the org settings.)"
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
