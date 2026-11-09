#!/usr/bin/env bash
# Read-only audit of one repository for the gha-hardening skill.
#
# Usage: audit.sh OWNER/REPO CLONE_DIR
#   CLONE_DIR is a local clone of OWNER/REPO, on any branch: the audit fetches
#   the default branch and reads it from a temporary worktree, so its counts
#   are the "before" of the pull request.
# Environment: GATE_REF and ZIZMOR_RUNNER, passed to gate-scan.sh;
#   AUDIT_APPS=1 also lists the App installations on selected repositories.
#
# Prints, section by section: the Gate scan (the exact scan the org ruleset
# runs) and the repository's own zizmor job, the checks on the default branch
# HEAD with the cause of each failed one, the workflows the default branch does
# not run with their last run, Actions settings, secrets and Environments,
# pushes and publishing, privileged triggers, registry logins, the rules on the
# default branch (rulesets and classic branch protection, with their required
# checks) and on tags with their bypass actors, the GitHub Apps installed on
# every repository, Renovate and Dependabot, and the dependency lock and
# cooldown of each project directory with the open vulnerability pull
# requests. It changes nothing. A section the token cannot read says so and
# moves on.
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
# Trigger names of a workflow file, one per line; a push limited to tags prints "push-tags".
workflow_triggers() {
  awk '
    /^.?on.?:/ {
      in_on = 1; rest = $0; sub(/^[^:]*:/, "", rest); sub(/#.*/, "", rest); gsub(/[][,]/, " ", rest)
      n = split(rest, names, " "); for (i = 1; i <= n; i++) seen[names[i]] = 1
      next }
    in_on && /^[^ #]/ { in_on = 0 }
    in_on && /^ +[^ #]/ {
      match($0, /^ +/); if (!indent) indent = RLENGTH
      key = $0; sub(/^ +(- *)?/, "", key); sub(/[: #].*/, "", key)
      if (RLENGTH == indent) { trigger = key; seen[key] = 1 }
      else if (trigger == "push" && key == "tags") push_tags = 1
      else if (trigger == "push" && key ~ /^branches/) push_branches = 1 }
    END {
      if (("push" in seen) && push_tags && !push_branches) { delete seen["push"]; seen["push-tags"] = 1 }
      for (key in seen) print key }' "$1" | sort
}
# Date (UTC, ISO 8601) of the last commit on the default branch that touched a path.
last_change() {
  if [[ $(git -C "${tree}" rev-parse --is-shallow-repository) == true ]]; then
    gh api "repos/${repo}/commits?path=$1&sha=${branch}&per_page=1" --jq '.[0].commit.committer.date // empty' 2>/dev/null
  else
    TZ=UTC git -C "${tree}" log -1 --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ -- "$1"
  fi
}
# The line of a config file setting a key, as "<line> (<file>)", or nothing.
setting_in() {
  local file
  for file in "${@:2}"; do
    [[ -f "${tree}/${file}" ]] && grep -m1 -hE "$1" "${tree}/${file}" | sed "s|\$| (${file})|" && return
  done
}
# Why a failed Actions job failed: its failed step, the last error line of its log
# before the post-job cleanup, and the same check on the latest pull request runs.
check_cause() {
  local job_id=$1 name=$2 job run_id step line workflow_id pr_runs pr_run pr_job=""
  if ! job=$(gh api "repos/${repo}/actions/jobs/${job_id}" \
    --jq '"\(.run_id)\t\([.steps[]? | select(.conclusion | IN("failure", "timed_out")) | .name] | first // "none")"' 2>/dev/null); then
    echo "    $(unreadable "job ${job_id}")"
    return
  fi
  run_id=${job%%$'\t'*}
  step=${job#*$'\t'}
  # The runner's closing "Process completed with exit code" line says nothing about the cause.
  line=$(gh run view "${run_id}" -R "${repo}" --job "${job_id}" --log-failed 2>/dev/null | cut -f3- \
    | sed -e '/Post job cleanup\./,$d' -e "s/"$'\x1b'"\[[0-9;]*m//g" -e 's/^[0-9-]*T[0-9:.]*Z //' \
    | grep -E 'error|Error|timed out|exit code' | grep -v 'Process completed with exit code' | tail -1 | cut -c1-200)
  echo "    failed step: ${step}; last error line: ${line:-none found}"
  workflow_id=$(gh api "repos/${repo}/actions/runs/${run_id}" --jq .workflow_id 2>/dev/null)
  pr_runs=$(gh api "repos/${repo}/actions/workflows/${workflow_id}/runs?event=pull_request&status=completed&per_page=5" \
    --jq '.workflow_runs[].id' 2>/dev/null)
  for pr_run in ${pr_runs}; do
    pr_job=$(gh api "repos/${repo}/actions/runs/${pr_run}/jobs?per_page=100" 2>/dev/null | jq -r --arg name "${name}" \
      'first(.jobs[] | select(.name == $name) | "\(.conclusion // .status), \((.completed_at // .started_at // "")[:10]), \(.html_url)")')
    [[ -n "${pr_job}" ]] && break
  done
  echo "    latest pull request run of ${name}: ${pr_job:-none in the last 5 pull request runs of its workflow}"
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
head_sha=$(git -C "${tree}" rev-parse HEAD)
rules=$(gh api "repos/${repo}/rules/branches/${branch}?per_page=100" 2>/dev/null || echo "[]")
echo "Audited: ${repo}@${branch} (${head_sha:0:7}, committed $(git -C "${tree}" log -1 --format=%cs HEAD))"

section "Gate scan"
bash "$(dirname "$0")/gate-scan.sh" "${repo}" "${tree}" 2>&1 | grep -v ' INFO '
echo "Gate scan exit status: ${PIPESTATUS[0]} (0 pass, 10-14 findings by highest severity, 1 or 2 scan error)"
own_zizmor=$(cd "${tree}" && for file in .github/workflows/*.y*ml; do
  [[ -f "${file}" ]] && awk -v f="${file}" '
    /^jobs:/ { in_jobs = 1; next }
    /^[^ #]/ { in_jobs = 0 }
    in_jobs && /^ +[A-Za-z0-9_-]+:/ {
      match($0, /^ +/); if (!indent) indent = RLENGTH
      if (RLENGTH == indent) { job = $0; sub(/^ +/, "", job); sub(/:.*/, "", job) } }
    in_jobs && job != "" { line = tolower($0); sub(/#.*/, "", line) }
    in_jobs && job != "" && line ~ /zizmor/ && !seen[job]++ { print "  " f ": job " job }' "${file}"
done)
echo "Own zizmor job on ${branch}:"
echo "${own_zizmor:-  none}"

section "Checks on ${branch} HEAD"
echo "Check runs (the latest of each name; under a failed Actions job, its cause and the same check on pull requests):"
if checks=$(gh api --paginate "repos/${repo}/commits/${head_sha}/check-runs?per_page=100" \
  --jq '.check_runs[] | [.started_at // "-", .name, .conclusion // .status, (.completed_at // .started_at // "-")[:10], .html_url, .id, .app.slug] | @tsv' 2>/dev/null); then
  while IFS=$'\t' read -r _ name conclusion date url id app; do
    echo "  ${name}: ${conclusion}, ${date}, ${url}"
    [[ "${conclusion}" =~ ^(failure|timed_out)$ && "${app}" == github-actions ]] && check_cause "${id}" "${name}"
  done < <(sort -r <<<"${checks}" | awk -F'\t' 'NF == 7 && !seen[$2]++')
  [[ -n "${checks}" ]] || echo "  none"
else
  echo "  $(unreadable "check runs")"
fi
statuses=$(gh api "repos/${repo}/commits/${head_sha}/status" \
  --jq '.statuses[] | "  \(.context): \(.state), \(.updated_at[:10]), \(.target_url // "no link")"' 2>/dev/null)
echo "Commit statuses:"
echo "${statuses:-  none}"

section "Workflows ${branch} does not run on push or pull request"
echo "Their last run and the last commit on ${branch} that changed the file; read each flagged one for steps that cannot work today:"
listed=""
for file in "${tree}"/.github/workflows/*.y*ml; do
  [[ -f "${file}" ]] || continue
  path="${file#"${tree}/"}"
  triggers=$(workflow_triggers "${file}")
  grep -qxE 'push|pull_request|merge_group' <<<"${triggers}" && continue
  listed=1
  changed=$(last_change "${path}")
  if [[ "${triggers}" == workflow_call ]]; then
    callers=$(cd "${tree}" && grep -lE "uses: *\./${path}" .github/workflows/*.y*ml 2>/dev/null | paste -sd, -)
    echo "  ${path} (workflow_call): runs inside its callers (${callers:-none on ${branch}}); changed ${changed:0:10}"
    continue
  fi
  run=$(gh api "repos/${repo}/actions/workflows/${file##*/}/runs?per_page=1" \
    --jq '.workflow_runs[0] // empty | "\(.created_at)\t\(.conclusion // .status) on \(.head_branch), \(.html_url)"' 2>/dev/null)
  if [[ -z "${run}" ]]; then
    last_run="none on record"; flag="; NO RUN ON RECORD"
  else
    last_run="${run:0:10} ${run#*$'\t'}"; flag=""
    [[ "${changed}" > "${run%%$'\t'*}" ]] && flag="; CHANGED SINCE ITS LAST RUN"
  fi
  echo "  ${path} ($(paste -sd, - <<<"${triggers}")): last run ${last_run}; changed ${changed:0:10}${flag}"
done
[[ -n "${listed}" ]] || echo "  none"

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
if ! protection=$(gh api "repos/${repo}/branches/${branch}" --jq .protection 2>/dev/null); then
  echo "classic branch protection: $(unreadable "branch ${branch}")"
elif [[ $(jq -r .enabled <<<"${protection}") != true ]]; then
  echo "classic branch protection: off"
else
  echo "classic branch protection: on"
  status_checks=$(jq -c '.required_status_checks // empty' <<<"${protection}")
  [[ -n "${status_checks}" ]] || status_checks=$(gh api "repos/${repo}/branches/${branch}/protection" \
    --jq '.required_status_checks // {"contexts": []}' 2>/dev/null)
  if [[ -z "${status_checks}" ]]; then
    echo "  required checks: $(unreadable "needs repository admin")"
  else
    jq -r 'if .enforcement_level == "off" or (.contexts | length) == 0 then "  required checks: none"
      else .contexts[] | "  required check: \(.)" end' <<<"${status_checks}"
  fi
fi

section "Rules on tags"
tag_rulesets=$(gh api "repos/${repo}/rulesets?includes_parents=true&targets=tag&per_page=100" --jq '.[].id' 2>/dev/null)
for id in ${tag_rulesets}; do
  gh api "repos/${repo}/rulesets/${id}" --jq '"ruleset \(.id) \(.name) (\(.source_type) \(.source), \(.enforcement))",
    "  refs: \(.conditions.ref_name.include | join(", ")); rules: \([.rules[].type] | join(", "))"' 2>/dev/null
done
[[ -n "${tag_rulesets}" ]] || echo "No ruleset protects tags."

section "GitHub Apps installed on every repository"
# Sensitive: write access that can change code, workflows, settings or secrets.
if installs=$(gh api "orgs/${owner}/installations?per_page=100" 2>/dev/null); then
  [[ "${AUDIT_APPS:-}" == 1 ]] && scope='["all", "selected"]' || scope='["all"]'
  jq -r --argjson scope "${scope}" '.installations[]
    | select(.suspended_at == null and (.repository_selection as $s | $scope | index($s)))
    | [.permissions | to_entries[]
        | select(.key | IN("contents", "workflows", "actions", "administration", "secrets", "environments"))
        | select(.value != "read") | "\(.key)=\(.value)"] as $sensitive
    | "\(if ($sensitive | length) > 0 and .repository_selection == "all" then "SENSITIVE " else "" end)"
      + "\(.app_slug): \(.repository_selection) repositories"
      + "\(if ($sensitive | length) > 0 then "; " + ($sensitive | join(", ")) else "; no write on code, workflows, settings or secrets" end)"' <<<"${installs}"
  echo "(SENSITIVE: write on code, workflows, settings or secrets of every repository.)"
  [[ "${AUDIT_APPS:-}" == 1 ]] || jq -r '[.installations[] | select(.suspended_at == null and .repository_selection == "selected")] | length
    | "\(.) more installations cover selected repositories; this token cannot tell whether they include this one (AUDIT_APPS=1 lists them)."' <<<"${installs}"
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
  echo "Settings ${config} shares with the shared preset (${owner}/.github default.json):"
  if ! preset=$(gh api "repos/${owner}/.github/contents/default.json" --jq .content 2>/dev/null | base64 -d 2>/dev/null) || [[ -z "${preset}" ]]; then
    unreadable "${owner}/.github default.json"
  elif ! jq -r --argjson p "${preset}" '
      ((.extends // [])[] | select(. as $e | $p.extends | index($e)) | "  extends \(.): the preset extends it"),
      (to_entries[] | select(.key | IN("$schema", "description", "extends", "packageRules") | not)
        | select(.key as $k | $p | has($k)) | "  \(.key): \(.value | tojson) (preset: \($p[.key] | tojson))"),
      ((.packageRules // [])[] | "  packageRules: \(tojson)"),
      (if .packageRules then $p.packageRules[] | "    preset rule: \(del(.description) | tojson)" else empty end)
    ' "${tree}/${config}" 2>/dev/null; then
    echo "  (not plain JSON: compare by hand)"
  fi
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

section "Dependencies"
echo "Each project directory on ${branch} (a workspace member under a lock of the same ecosystem is part of that project):"
manifests=$(git -C "${tree}" ls-files -- ':(glob)**/pyproject.toml' ':(glob)**/requirements*.txt' ':(glob)**/Pipfile' \
  ':(glob)**/package.json' ':(glob)**/go.mod' ':(glob)**/Cargo.toml')
locks=$(git -C "${tree}" ls-files -- ':(glob)**/uv.lock' ':(glob)**/poetry.lock' ':(glob)**/pdm.lock' ':(glob)**/Pipfile.lock' \
  ':(glob)**/pnpm-lock.yaml' ':(glob)**/package-lock.json' ':(glob)**/yarn.lock' ':(glob)**/bun.lock' ':(glob)**/bun.lockb')
ecosystem_of() {
  case "${1##*/}" in
    package.json | pnpm-lock.yaml | package-lock.json | yarn.lock | bun.lock | bun.lockb) echo js ;;
    go.mod) echo go ;;
    Cargo.toml) echo rust ;;
    *) echo python ;;
  esac
}
lock_dirs=$(while read -r lock; do [[ -n "${lock}" ]] && echo "$(ecosystem_of "${lock}") $(dirname "${lock}")"; done <<<"${locks}")
projects=$(while read -r manifest; do
  [[ -n "${manifest}" ]] || continue
  eco=$(ecosystem_of "${manifest}")
  dir=$(dirname "${manifest}")
  parent="${dir}"
  if ! grep -qx "${eco} ${dir}" <<<"${lock_dirs}"; then
    while [[ "${parent}" != . ]]; do
      parent=$(dirname "${parent}")
      grep -qx "${eco} ${parent}" <<<"${lock_dirs}" && continue 2
    done
  fi
  echo "${eco} ${dir}"
done <<<"${manifests}" | sort -u)
has() { [[ -f "${tree}/${p}$1" ]]; }
while read -r eco dir; do
  [[ -n "${eco}" ]] || continue
  [[ "${dir}" == . ]] && p="" || p="${dir}/"
  case "${eco}" in
    python)
      if has uv.lock || grep -qs '^\[tool\.uv' "${tree}/${p}pyproject.toml"; then manager=uv
      elif has poetry.lock || grep -qs '^\[tool\.poetry' "${tree}/${p}pyproject.toml"; then manager=poetry
      elif has pdm.lock; then manager=pdm
      elif has Pipfile; then manager=pipenv
      elif has pyproject.toml && grep -rqsE 'setup-uv|uv (sync|run|pip|build)' "${tree}/.github"; then manager="uv (from CI)"
      elif has pyproject.toml; then manager="pyproject.toml, manager unknown"
      else manager="pip (requirements files)"; fi ;;
    js)
      pm=$(grep -hoE '"packageManager": *"[a-z]+' "${tree}/${p}package.json" | sed 's/.*"//')
      if has pnpm-lock.yaml || has pnpm-workspace.yaml || [[ "${pm}" == pnpm ]]; then manager=pnpm
      elif has yarn.lock || has .yarnrc.yml || [[ "${pm}" == yarn ]]; then manager=yarn
      elif has bun.lock || has bun.lockb || has bunfig.toml || [[ "${pm}" == bun ]]; then manager=bun
      else manager=npm; fi ;;
    go) manager=go ;;
    rust) manager=cargo ;;
  esac
  case "${manager}" in
    uv*) lock=uv.lock; cooldown=$(setting_in '^exclude-newer *=' "${p}pyproject.toml" "${p}uv.toml" uv.toml) ;;
    poetry) lock=poetry.lock; cooldown=$(setting_in 'min-release-age' "${p}poetry.toml" poetry.toml) ;;
    pnpm) lock=pnpm-lock.yaml; cooldown=$(setting_in '^minimum-?[Rr]elease-?[Aa]ge[:=]' "${p}pnpm-workspace.yaml" pnpm-workspace.yaml "${p}.npmrc" .npmrc) ;;
    npm) lock=package-lock.json; cooldown=$(setting_in '^min-release-age' "${p}.npmrc" .npmrc) ;;
    yarn) lock=yarn.lock; cooldown=$(setting_in 'npmMinimalAgeGate' "${p}.yarnrc.yml" .yarnrc.yml) ;;
    bun) lock=bun.lock; cooldown=$(setting_in 'minimumReleaseAge' "${p}bunfig.toml" bunfig.toml) ;;
    pdm) lock=pdm.lock; cooldown="" ;;
    pipenv) lock=Pipfile.lock; cooldown="" ;;
    go) lock=go.sum; cooldown="" ;;
    cargo) lock=Cargo.lock; cooldown="" ;;
    *) lock=""; cooldown=$(cd "${tree}" && grep -rhoE -m1 -- '--uploaded-prior-to[= ]\S+' .github 2>/dev/null | head -1) ;;
  esac
  [[ "${manager}" == bun ]] && has bun.lockb && lock=bun.lockb
  if [[ -z "${lock}" ]]; then lock_state="no lock file"
  elif has "${lock}"; then lock_state="${lock} committed"
  elif git -C "${tree}" check-ignore -q --no-index "${p}${lock}"; then lock_state="${lock} gitignored"
  else lock_state="no ${lock}"; fi
  echo "  ${dir}: ${manager}; ${lock_state}; cooldown: ${cooldown:-none}"
done <<<"${projects}"
[[ -n "${projects}" ]] || echo "  none"
