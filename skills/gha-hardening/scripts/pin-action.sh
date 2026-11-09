#!/usr/bin/env bash
# Prints the pinned form of an action reference: OWNER/REPO[/PATH]@SHA # vX.Y.Z
#
# Usage: pin-action.sh OWNER/REPO[/PATH]@REF
#   REF  a version tag (v4.2.2), a major tag or branch (v4, release/v1), or a SHA.
# A major tag or branch resolves to the highest vMAJOR.x.y release tag; a SHA
# keeps its commit and gets the most specific tag pointing at it. Annotated
# tags resolve to their commit, which is what the runner checks out.
set -euo pipefail

ref_in="${1:?usage: pin-action.sh OWNER/REPO[/PATH]@REF}"
action="${ref_in%@*}"
ref="${ref_in##*@}"
repo=$(cut -d/ -f1-2 <<<"${action}")

# "sha tag" for every tag, annotated tags peeled to their commit.
tags=$(git ls-remote --tags "https://github.com/${repo}.git" | awk '
  { name = $2; sub("refs/tags/", "", name) }
  name ~ /\^\{\}$/ { sub(/\^\{\}$/, "", name); peeled[name] = $1; next }
  { plain[name] = $1 }
  END { for (n in plain) print (n in peeled ? peeled[n] : plain[n]), n }')
semver() { grep -E ' v?[0-9]+\.[0-9]+\.[0-9]+$' || true; }

if [[ "${ref}" =~ ^[0-9a-f]{40}$ ]]; then
  sha="${ref}"
  tag=$(awk -v s="${sha}" '$1 == s { print $2 }' <<<"${tags}" | sort -V | tail -1)
elif awk -v r="${ref}" '$2 == r' <<<"${tags}" | grep -qE ' v?[0-9]+\.[0-9]+\.[0-9]+$'; then
  read -r sha tag < <(awk -v r="${ref}" '$2 == r' <<<"${tags}")
elif [[ "${ref}" =~ v?([0-9]+)$ ]]; then
  major="${BASH_REMATCH[1]}"
  read -r sha tag < <(semver <<<"${tags}" | grep -E " v?${major}\.[0-9]+\.[0-9]+$" | sort -k2 -V | tail -1)
else
  sha=$(git ls-remote "https://github.com/${repo}.git" "refs/heads/${ref}" "refs/tags/${ref}" | head -1 | cut -f1)
  tag=""
fi

if [[ -z "${sha:-}" ]]; then
  echo "Cannot resolve ${ref_in}" >&2
  exit 1
fi
if [[ -z "${tag:-}" ]]; then
  echo "${action}@${sha} # ${ref} (no release tag: ask a human whether to trust this commit)"
else
  echo "${action}@${sha} # ${tag}"
fi
