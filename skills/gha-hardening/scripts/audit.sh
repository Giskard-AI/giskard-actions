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
# HEAD (stale ones flagged) with their latest pull request runs by actor and the
# cause of each failed run, the workflows the default branch does not run with
# their last run, Actions settings, secrets and Environments with the deploy
# keys of the repositories cloned over SSH, pushes and publishing, privileged
# triggers, registry logins, the rules on the default branch (rulesets and
# classic branch protection, with their required checks, and the squash merge
# message) and on tags with their bypass actors, the GitHub Apps installed on every repository, Renovate
# and Dependabot, and the manager, lock and cooldown of each project directory
# with the installs outside the lock and the open vulnerability pull requests,
# and the open pull requests that change files the hardening may change.
# It changes nothing. A list with nothing in it prints "none"; one the token
# cannot read says so.
set -uo pipefail

repo="${1:?usage: audit.sh OWNER/REPO CLONE_DIR}"
clone=$(cd "${2:?usage: audit.sh OWNER/REPO CLONE_DIR}" && pwd)
owner="${repo%%/*}"

section() { printf '\n## %s\n\n' "$1"; }
unreadable() { echo "(unreadable with this token: $1)"; }
# jq: the first failed step of an Actions job; GitHub drops the steps of old jobs.
failed_step='if (.steps // []) == [] then "unknown" else ([.steps[] | select(.conclusion | IN("failure", "timed_out")) | .name] | first // "none") end'
# Each line under `jobs:` of a workflow file, as "<job key><TAB><line>".
job_lines() {
  awk '
    /^jobs:/ { in_jobs = 1; next }
    /^[^ #]/ { in_jobs = 0 }
    in_jobs && /^ +[A-Za-z0-9_-]+:/ {
      match($0, /^ +/); if (!indent) indent = RLENGTH
      if (RLENGTH == indent) { job = $0; sub(/^ +/, "", job); sub(/:.*/, "", job) } }
    in_jobs && job != "" { print job "\t" $0 }' "$1"
}
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
# Why a failed Actions job failed: its failed step and the last error line of its
# log before the post-job cleanup.
job_cause() {
  local run_id=$1 job_id=$2 step=$3 log line
  if log=$(gh run view "${run_id}" -R "${repo}" --job "${job_id}" --log-failed 2>/dev/null); then
    # The runner's closing "Process completed with exit code" line says nothing about the cause.
    line=$(cut -f3- <<<"${log}" \
      | sed -e '/Post job cleanup\./,$d' -e "s/"$'\x1b'"\[[0-9;]*m//g" -e 's/^[0-9-]*T[0-9:.]*Z //' \
      | grep -E 'error|Error|timed out|exit code' | grep -v 'Process completed with exit code' | tail -1 | cut -c1-200)
  else
    line="log unavailable (expired or unreadable)"
  fi
  echo "failed step: ${step}; last error line: ${line:-none found}"
}
# The jobs of the last 10 completed pull request runs of a workflow, newest first, as
# "<name> <actor> <conclusion> <date> <url> <run id> <job id> <failed step>" (tab-separated).
pr_jobs() {
  local workflow_id=$1 run actor
  if [[ ! -f "${cache}/${workflow_id}" ]]; then
    gh api "repos/${repo}/actions/workflows/${workflow_id}/runs?event=pull_request&status=completed&per_page=10" \
      --jq '.workflow_runs[] | "\(.id)\t\(.actor.login)"' 2>/dev/null | while IFS=$'\t' read -r run actor; do
      gh api "repos/${repo}/actions/runs/${run}/jobs?per_page=100" 2>/dev/null | jq -r --arg actor "${actor}" ".jobs[]
        | [.name, \$actor, .conclusion // .status, (.completed_at // .started_at // \"-\")[:10], .html_url, .run_id, .id,
           (${failed_step})] | @tsv"
    done >"${cache}/${workflow_id}"
  fi
  cat "${cache}/${workflow_id}"
}
# A check's pull request runs, split by actor, with the cause of the latest one when it failed.
# Names match whatever their case: the pull request may rename `build` to `Build`.
pr_runs_of() {
  local name=$1 workflow_id=$2 runs actor subset counts conclusion date url run_id job_id step
  runs=$(pr_jobs "${workflow_id}" | awk -F'\t' -v n="${name}" 'tolower($1) == tolower(n)')
  for actor in others 'dependabot[bot]'; do
    subset=$(awk -F'\t' -v a="${actor}" '(a == "others") == ($2 != "dependabot[bot]")' <<<"${runs}")
    if [[ -z "${subset}" ]]; then
      [[ "${actor}" == others ]] && echo "    pull request runs by others: none"
      continue
    fi
    counts=$(cut -f3 <<<"${subset}" | sort | uniq -c | sort -rn | awk '{ printf "%s%d %s", sep, $1, $2; sep = ", " }')
    IFS=$'\t' read -r _ _ conclusion date url run_id job_id step <<<"$(head -1 <<<"${subset}")"
    echo "    pull request runs by ${actor}: ${counts}; latest: ${conclusion}, ${date}, ${url}"
    [[ "${conclusion}" =~ ^(failure|timed_out)$ ]] && echo "      $(job_cause "${run_id}" "${job_id}" "${step}")"
  done
}
# Whether a workflow that uses uv runs in a project directory: `working-directory`, `cd`,
# `--directory`/`--project` or a path under it. Every workflow runs in the root directory.
runs_uv_in() {
  local file escaped
  # shellcheck disable=SC2001,SC2016 # escape the regex characters of a path
  escaped=$(sed 's/[][\.*^$()+?{|]/\\&/g' <<<"$1")
  for file in "${tree}"/.github/workflows/*.y*ml; do
    if [[ ! -f "${file}" ]] || ! grep -qE 'setup-uv|uv (sync|run|pip|build|lock)' "${file}"; then continue; fi
    [[ "$1" == . ]] && return 0
    grep -qE "([ =:'\"]|^)(\./)?${escaped}(/|['\"]?( |$))" "${file}" && return 0
  done
  return 1
}
# The state of a committed uv.lock: `uv lock --check` offline, then online when
# deciding needs packages or git sources the uv cache does not have.
uv_lock_state() {
  local out online=""
  command -v uv >/dev/null || { echo "uv lock --check not run: uv is not installed"; return; }
  out=$(cd "$1" && UV_PYTHON_DOWNLOADS=never uv lock --check --offline 2>&1) && { echo "lock up to date"; return; }
  if grep -qi 'network' <<<"${out}"; then
    online=" (checked online)"
    out=$(cd "$1" && UV_PYTHON_DOWNLOADS=never uv lock --check 2>&1) && { echo "lock up to date${online}"; return; }
  fi
  if grep -q 'needs to be updated' <<<"${out}"; then
    echo "lock out of date${online}"
  else
    echo "uv lock --check failed${online}: $(grep -m1 -E '^error' <<<"${out}" | cut -c1-200)"
  fi
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
cache=$(mktemp -d)
trap 'git -C "${clone}" worktree remove --force "${tree}" 2>/dev/null; rm -rf "${tree}" "${cache}"' EXIT
git -C "${clone}" worktree add -q --detach "${tree}" "origin/${branch}" || exit 2
head_sha=$(git -C "${tree}" rev-parse HEAD)
echo "Audited: ${repo}@${branch} (${head_sha:0:7}, committed $(git -C "${tree}" log -1 --format=%cs HEAD))"

section "Gate scan"
bash "$(dirname "$0")/gate-scan.sh" "${repo}" "${tree}" 2>&1 | grep -v ' INFO '
echo "Gate scan exit status: ${PIPESTATUS[0]} (0 pass, 10-14 findings by highest severity, 1 or 2 scan error)"
own_zizmor=$(cd "${tree}" && for file in .github/workflows/*.y*ml; do
  [[ -f "${file}" ]] && job_lines "${file}" | awk -F'\t' -v f="${file}" '
    { line = tolower($2); sub(/#.*/, "", line) }
    line ~ /zizmor/ && !seen[$1]++ { print "  " f ": job " $1 }'
done)
echo "Own zizmor job on ${branch}:"
echo "${own_zizmor:-  none}"

section "Checks on ${branch} HEAD"
echo "Check runs, the latest of each name (STALE: ${branch} last ran it more than 30 days ago). Under an Actions check:"
echo "the last 10 pull request runs of its workflow by actor (a Dependabot run reads only Dependabot secrets,"
echo "reference.md section 11), and the cause of each latest failed run:"
if checks=$(gh api --paginate "repos/${repo}/commits/${head_sha}/check-runs?per_page=100" \
  --jq '.check_runs[] | [.started_at // "-", .name, .conclusion // .status, (.completed_at // .started_at // "-")[:10], .html_url, .id, .app.slug] | @tsv' 2>/dev/null); then
  while IFS=$'\t' read -r _ name conclusion date url id app; do
    echo "  ${name}: ${conclusion}, ${date}, ${url}"
    run_id="" step="" workflow_id="" event=""
    if [[ "${app}" == github-actions ]]; then
      if job=$(gh api "repos/${repo}/actions/jobs/${id}" \
        --jq "[.run_id, (${failed_step})] | @tsv" 2>/dev/null); then
        IFS=$'\t' read -r run_id step <<<"${job}"
        IFS=$'\t' read -r workflow_id event < <(gh api "repos/${repo}/actions/runs/${run_id}" --jq '[.workflow_id, .event] | @tsv' 2>/dev/null)
      else
        echo "    $(unreadable "job ${id}")"
      fi
    fi
    # Dependabot's own update jobs run outside commits and pull requests.
    [[ "${event}" == dynamic ]] && continue
    jq -ne --arg d "${date}" '($d | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")) and (($d + "T00:00:00Z" | fromdateiso8601) < now - 2592000)' >/dev/null \
      && echo "    STALE: ${branch} has not run it since ${date}"
    [[ "${conclusion}" =~ ^(failure|timed_out)$ && -n "${run_id}" ]] && echo "    $(job_cause "${run_id}" "${id}" "${step}")"
    [[ -n "${workflow_id}" ]] && pr_runs_of "${name}" "${workflow_id}"
  done < <(sort -r <<<"${checks}" | awk -F'\t' 'NF == 7 && !seen[$2]++')
  [[ -n "${checks}" ]] || echo "  none"
else
  echo "  $(unreadable "check runs")"
fi
echo "Commit statuses:"
if statuses=$(gh api "repos/${repo}/commits/${head_sha}/status" \
  --jq '.statuses[] | "  \(.context): \(.state), \(.updated_at[:10]), \(.target_url // "no link")"' 2>/dev/null); then
  echo "${statuses:-  none}"
else
  echo "  $(unreadable "commit statuses")"
fi

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
  [[ -n "${repo_secrets}" ]] || echo "  none"
else
  echo "  $(unreadable "needs repository admin")"
fi
echo "Organization secrets visible to this repository:"
if org_secrets=$(gh api "repos/${repo}/actions/organization-secrets" --jq '.secrets[] | "  \(.name)"' 2>/dev/null); then
  echo "${org_secrets:-  none}"
else
  echo "  $(unreadable "needs repository admin")"
fi
echo "Environments (secrets of an Environment deployable from any branch are readable from any branch):"
if envs=$(gh api "repos/${repo}/environments" --jq '.environments[].name' 2>/dev/null); then
  for env in ${envs}; do
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
    for kind in secrets variables; do
      if names=$(gh api "repos/${repo}/environments/${env}/${kind}" --jq ".${kind}[].name" 2>/dev/null); then
        echo "    ${kind}: $(paste -sd, - <<<"${names}" | sed 's/,/, /g; s/^$/none/')"
      else
        echo "    ${kind}: $(unreadable "needs repository admin")"
      fi
    done
  done
  [[ -n "${envs}" ]] || echo "  none"
else
  echo "  $(unreadable "environments")"
fi
echo "Secrets referenced by workflows and actions:"
sed 's/^/  /' <<<"${referenced:-none}"
echo "Clone keys: a secret passed to webfactory/ssh-agent or ssh-key:, the repositories its job clones over SSH"
echo "(in the job, or the manifests and locks of its working directories), and each one's deploy keys (reference.md section 8):"
clone_keys=""
for file in "${tree}"/.github/workflows/*.y*ml; do
  [[ -f "${file}" ]] || continue
  lines=$(job_lines "${file}")
  while read -r job; do
    [[ -n "${job}" ]] || continue
    clone_keys=1
    text=$(awk -F'\t' -v j="${job}" '$1 == j { print $2 }' <<<"${lines}")
    keys=$(grep -oE '(ssh-private-key|ssh-key):.*secrets\.[A-Za-z0-9_]+' <<<"${text}" | grep -oE '[A-Za-z0-9_]+$' | sort -u | paste -sd, -)
    dirs=$(grep -oE 'working-directory: *[^ #]+' <<<"${text}" | sed -E "s/.*: *//; s/[\"']//g" | sort -u)
    cloned=$( { echo "${text}"; for dir in ${dirs}; do
        cat "${tree}/${dir}"/{pyproject.toml,uv.lock,package.json} "${tree}/${dir}"/requirements*.txt 2>/dev/null; done; } \
      | grep -oE 'git@github\.com[:/][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+' | sed -E 's/^git@github\.com[:/]//; s/\.git$//'
      grep -oE 'repository: *[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+' <<<"${text}" | sed 's/.*: *//')
    echo "  ${file#"${tree}/"}: job ${job} reads ${keys}"
    while read -r cloned_repo; do
      [[ -n "${cloned_repo}" ]] || continue
      if deploy_keys=$(gh api "repos/${cloned_repo}/keys" --jq '.[]
        | "      \(.title): read_only \(.read_only), added \(.created_at[:10])\(if .last_used then ", last used \(.last_used[:10])" else "" end)"' 2>/dev/null); then
        echo "    ${cloned_repo} deploy keys:"
        echo "${deploy_keys:-      none (the secret is not a deploy key: it reaches whatever its account can)}"
      else
        echo "    ${cloned_repo} deploy keys: unknown (needs admin on ${cloned_repo})"
      fi
    done < <(sort -u <<<"${cloned}")
    [[ -n "${cloned}" ]] || echo "    no repository cloned over SSH found: read the job"
  done < <(awk -F'\t' '$2 ~ /(ssh-private-key|ssh-key):.*secrets\./ { print $1 }' <<<"${lines}" | sort -u)
done
[[ -n "${clone_keys}" ]] || echo "  none"

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
if ! rules=$(gh api "repos/${repo}/rules/branches/${branch}?per_page=100" 2>/dev/null); then
  echo "rulesets: $(unreadable "rules on ${branch}")"
  rules="[]"
elif [[ $(jq length <<<"${rules}") == 0 ]]; then
  echo "rulesets: none"
fi
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
# The merge settings come back null to a token without push access.
IFS=$'\t' read -r allowed title message < <(gh api "repos/${repo}" \
  --jq '[.allow_squash_merge, .squash_merge_commit_title, .squash_merge_commit_message] | @tsv' 2>/dev/null)
if [[ -z "${allowed}" ]]; then
  echo "Squash merge: $(unreadable "needs push access")"
else
  hint=""
  [[ "${allowed}" == true && "${title}/${message}" != PR_TITLE/PR_BODY ]] \
    && hint="; set PR_TITLE / PR_BODY (Human Step, reference.md section 10)"
  echo "Squash merge: allowed=${allowed}, title=${title}, message=${message}${hint}"
fi

section "Rules on tags"
if tag_rulesets=$(gh api "repos/${repo}/rulesets?includes_parents=true&targets=tag&per_page=100" --jq '.[].id' 2>/dev/null); then
  for id in ${tag_rulesets}; do
    gh api "repos/${repo}/rulesets/${id}" --jq '"ruleset \(.id) \(.name) (\(.source_type) \(.source), \(.enforcement))",
      "  refs: \(.conditions.ref_name.include | join(", ")); rules: \([.rules[].type] | join(", "))"' 2>/dev/null \
      || echo "ruleset ${id}: $(unreadable "ruleset ${id}")"
  done
  [[ -n "${tag_rulesets}" ]] || echo "No ruleset protects tags."
else
  echo "Tag rulesets: $(unreadable "rulesets")"
fi

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
  [[ -z $(tail -c1 "${tree}/${config}") ]] || echo
  echo "Settings ${config} shares with the shared preset (${owner}/.github default.json), then the ones it keeps:"
  if ! preset=$(gh api "repos/${owner}/.github/contents/default.json" --jq .content 2>/dev/null | base64 -d 2>/dev/null) || [[ -z "${preset}" ]]; then
    unreadable "${owner}/.github default.json"
  # A repository rule duplicates a preset rule when it sets a key of the preset rule to the
  # same value and each matcher they both have selects a common value ("/^x$/" counts as x).
  elif ! jq -r --argjson p "${preset}" '
      def norm: if type == "string" and test("^/\\^.*\\$/$") then .[2:-2] else . end;
      def values: if type == "array" then map(norm) else [norm] end;
      def overlap($a; $b): ($a | values) as $x | ($b | values) as $y
        | any($x[], $y[]; . == "*") or any($x[]; . as $v | any($y[]; . == $v));
      def effects: with_entries(select(.key | (startswith("match") or . == "description") | not));
      def duplicates($rule):
        (effects | to_entries | any(.[]; .key as $k | .value as $v | $rule | effects | has($k) and .[$k] == $v))
        and (to_entries | all(.[] | select(.key | startswith("match")); .key as $k | .value as $v
          | ($rule | has($k) | not) or overlap($v; $rule[$k])));
      def own_keys: to_entries[] | select(.key | IN("$schema", "description", "extends", "packageRules") | not);
      [((.extends // [])[] | select(. as $e | any($p.extends[]; . == $e)) | "  extends \(.): the preset extends it"),
       (own_keys | select(.key as $k | $p | has($k)) | "  \(.key): \(.value | tojson) (preset: \($p[.key] | tojson))"),
       ((.packageRules // [])[] | . as $r | [$p.packageRules[] | select(. as $q | $r | duplicates($q))]
         | select(length > 0) | "  packageRules: \($r | tojson)",
           (.[] | "    duplicates preset rule: \(del(.description) | tojson)"))] as $shared
      | [((.extends // [])[] | select(. as $e | any($p.extends[]; . == $e) | not) | "  extends \(.)"),
         (own_keys | select(.key as $k | $p | has($k) | not) | "  \(.key): \(.value | tojson)"),
         ((.packageRules // [])[] | . as $r | select(any($p.packageRules[]; . as $q | $r | duplicates($q)) | not)
           | "  packageRules: \(tojson)")] as $kept
      | if $shared == [] then "  none" else $shared[] end,
        "Kept (own rules):",
        if $kept == [] then "  none" else $kept[] end
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
security_updates=$(gh api "repos/${repo}" --jq '.security_and_analysis.dependabot_security_updates.status // empty' 2>/dev/null)
echo "Dependabot security updates (they overlap with Renovate's vulnerability pull requests, reference.md section 14): ${security_updates:-$(unreadable "needs repository admin")}"
if gh api "repos/${repo}/vulnerability-alerts" --silent 2>/dev/null; then
  echo "Dependabot alerts: enabled"
else
  echo "Dependabot alerts: disabled, or $(unreadable "needs repository admin")"
fi

section "Dependencies"
echo "Each project directory on ${branch} (a workspace member under a lock of the same ecosystem is part of that project),"
echo "with \`uv lock --check\` for each committed uv.lock:"
manifests=$(git -C "${tree}" ls-files -- ':(glob)**/pyproject.toml' ':(glob)**/requirements*.txt' ':(glob)**/requirements*.in' \
  ':(glob)**/Pipfile' ':(glob)**/package.json' ':(glob)**/go.mod' ':(glob)**/Cargo.toml')
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
      if has uv.lock; then manager=uv
      elif grep -qs '^\[tool\.rye\]' "${tree}/${p}pyproject.toml" || grep -qs 'generated by rye' "${tree}/${p}requirements.lock"; then manager=rye
      elif has poetry.lock || grep -qs '^\[tool\.poetry' "${tree}/${p}pyproject.toml"; then manager=poetry
      elif has pdm.lock; then manager=pdm
      elif has Pipfile; then manager=pipenv
      elif grep -qs '^\[tool\.uv' "${tree}/${p}pyproject.toml"; then manager=uv
      elif compgen -G "${tree}/${p}requirements*.in" >/dev/null || grep -qs 'pip-compile' "${tree}/${p}"requirements*.txt; then manager=pip-tools
      elif has pyproject.toml && runs_uv_in "${dir}"; then manager="uv (from CI)"
      elif has pyproject.toml; then manager="pyproject.toml, manager unknown"
      else manager=pip; fi ;;
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
    rye) lock=requirements.lock; cooldown="" ;;
    pip-tools) lock=requirements.txt; cooldown="" ;;
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
  [[ "${lock_state}" == "uv.lock committed" ]] && lock_state+=", $(uv_lock_state "${tree}/${p:-.}")"
  case "${manager}" in
    rye | poetry | pdm | pipenv | pip-tools | pip) convert="; convert to uv (reference.md section 15)" ;;
    *) convert="" ;;
  esac
  echo "  ${dir}: ${manager}; ${lock_state}; cooldown: ${cooldown:-none}${convert}"
done <<<"${projects}"
[[ -n "${projects}" ]] || echo "  none"
echo "Installs outside the lock (each escapes the lock and the cooldown unless it installs a lock file; reference.md section 15):"
find_in_workflows '(^|[^A-Za-z0-9_-])pip3? install'
echo "Open vulnerability pull requests, with the upload date of each proposed version (YOUNGER THAN 7 DAYS: exempt it from the cooldown in that pull request):"
# shellcheck disable=SC2016 # jq variables
# Renovate's body links an age badge per update: .../badges/age/<datasource>/<package>/<version>?...
if vulnerable=$(gh pr list -R "${repo}" --state open --limit 100 --json number,title,labels,body --jq '.[]
    | select((.title | contains("[SECURITY]")) or any(.labels[]; .name == "security"))
    | ([.body | scan("badges/age/([^/]+)/([^?]+)/([^/?]+)\\?")] | if . == [] then [["-", "-", "-"]] else . end)[] as [$datasource, $package, $version]
    | [.number, $datasource, ($package | gsub("%40"; "@") | gsub("%2[Ff]"; "/")), $version, .title] | @tsv' 2>/dev/null); then
  while IFS=$'\t' read -r number datasource dependency version title; do
    case "${datasource}" in
      pypi) uploaded=$(curl -fsSL "https://pypi.org/pypi/${dependency}/${version}/json" 2>/dev/null \
        | jq -r '[.urls[].upload_time_iso_8601] | min // empty') ;;
      npm) uploaded=$(curl -fsSL "https://registry.npmjs.org/${dependency}" 2>/dev/null | jq -r --arg v "${version}" '.time[$v] // empty') ;;
      "-") echo "  #${number} ${title}: no version found in its body, check by hand"; continue ;;
      *) echo "  #${number} ${datasource} ${dependency} ${version}: upload date not checked"; continue ;;
    esac
    age=$(jq -nr --arg t "${uploaded}" 'if $t == "" then "upload date not found"
      else "uploaded \($t[:10])\(if ($t | sub("\\.[0-9]+"; "") | fromdateiso8601) > now - 604800 then "; YOUNGER THAN 7 DAYS" else "" end)" end')
    echo "  #${number} ${datasource} ${dependency} ${version}: ${age}"
  done < <(sed '/^$/d' <<<"${vulnerable}")
  [[ -n "${vulnerable}" ]] || echo "  none"
else
  echo "  $(unreadable "pull requests")"
fi

section "Open pull requests"
echo "Each one that changes files this hardening may change (workflows, actions, Renovate and Dependabot config,"
echo "manifests and locks), with those files: check its conflicts before opening the pull request (SKILL.md step 4):"
hardened='^\.github/|(^|/)(action\.ya?ml|renovate\.json5?|\.renovaterc(\.json)?|pyproject\.toml|uv\.toml|uv\.lock|poetry\.lock|Pipfile(\.lock)?|requirements[^/]*\.(txt|in)|package\.json|package-lock\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|\.npmrc|\.python-version|\.gitignore)$'
if open_prs=$(gh pr list -R "${repo}" --state open --limit 100 \
  --json number,title,author,isDraft,headRefName --jq '.[] | [.number, .author.login, (if .isDraft then "draft" else "ready" end), .headRefName, .title] | @tsv' 2>/dev/null); then
  touching=""
  while IFS=$'\t' read -r number author state head title; do
    [[ -n "${number}" ]] || continue
    files=$(gh pr diff "${number}" -R "${repo}" --name-only 2>/dev/null | grep -E "${hardened}")
    [[ -n "${files}" ]] || continue
    touching=1
    echo "  #${number} ${title} (${author}, ${state}, branch ${head}): $(paste -sd, - <<<"${files}" | sed 's/,/, /g')"
  done <<<"${open_prs}"
  [[ -n "${touching}" ]] || echo "  none"
else
  echo "  $(unreadable "pull requests")"
fi
