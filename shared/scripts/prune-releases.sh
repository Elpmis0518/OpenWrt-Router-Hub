#!/usr/bin/env bash
# Keep only the latest N releases (and their Git tags) matching a specific tag prefix.
# Strictly isolated: releases with any other prefix are never touched.
set -euo pipefail

PREFIX="${1:?Usage: prune-releases.sh <tag_prefix> [keep_count]}"
KEEP="${2:-3}"
REPO="${GH_REPO:-${GITHUB_REPOSITORY:-}}"

if [ -z "${REPO}" ]; then
  echo "ERROR: GH_REPO or GITHUB_REPOSITORY must be set." >&2
  exit 1
fi

if ! [[ "${KEEP}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: keep_count must be a positive integer (got '${KEEP}')." >&2
  exit 1
fi

echo "Pruning releases in ${REPO} with prefix '${PREFIX}' (keeping latest ${KEEP})..."

OLD_TAGS="$(
  gh api --paginate "repos/${REPO}/releases?per_page=100" |
    jq -sr --arg prefix "${PREFIX}" --argjson keep "${KEEP}" '
      add
      | [ .[] | select(.tag_name | startswith($prefix)) ]
      | sort_by(.created_at) | reverse
      | .[$keep:][]
      | .tag_name
    '
)"

if [ -z "${OLD_TAGS}" ]; then
  echo "No old releases to prune for prefix '${PREFIX}'."
  exit 0
fi

while IFS= read -r TAG; do
  [ -n "${TAG}" ] || continue
  # Defense-in-depth prefix guard
  case "${TAG}" in
    "${PREFIX}"*) ;;
    *)
      echo "Refusing to delete non-matching tag '${TAG}'!" >&2
      exit 1
      ;;
  esac
  echo "Deleting old release and tag: ${TAG}"
  gh release delete "${TAG}" --repo "${REPO}" --yes --cleanup-tag || true
  gh api -X DELETE "repos/${REPO}/git/refs/tags/${TAG}" >/dev/null 2>&1 || true
done <<< "${OLD_TAGS}"
