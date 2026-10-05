#!/usr/bin/env bash
# Standing rule: public repos are for viewing, not collaboration.
# Anyone may open issues; only the owner and their agents create branches/PRs/changes.
# Usage: harden-public-repo.sh OWNER/REPO   (idempotent; run right after creating a public repo)
set -euo pipefail
repo="${1:?usage: $0 OWNER/REPO}"
vis=$(gh api "repos/$repo" --jq .visibility)
[ "$vis" = "public" ] || { echo "$repo is $vis, not public: nothing to do"; exit 0; }

gh api -X PATCH "repos/$repo" -f pull_request_creation_policy=collaborators_only \
  -F has_wiki=false -F has_projects=false >/dev/null
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<JSON
{"required_status_checks":null,"enforce_admins":false,"required_pull_request_reviews":null,
 "restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
JSON
branch=$(gh api "repos/$repo" --jq .default_branch)
gh api -X PUT "repos/$repo/branches/$branch/protection" --input "$tmp" >/dev/null
gh api -X PUT "repos/$repo/vulnerability-alerts" >/dev/null || true
gh api -X PUT "repos/$repo/automated-security-fixes" >/dev/null || true

echo "== $repo"
gh api "repos/$repo" --jq '"issues=\(.has_issues) wiki=\(.has_wiki) projects=\(.has_projects) pr_creation=\(.pull_request_creation_policy) secret_scanning=\(.security_and_analysis.secret_scanning.status) push_protection=\(.security_and_analysis.secret_scanning_push_protection.status) dependabot=\(.security_and_analysis.dependabot_security_updates.status)"'
gh api "repos/$repo/branches/$branch/protection" --jq '"main: force_push=\(.allow_force_pushes.enabled) deletions=\(.allow_deletions.enabled)"'
echo "collaborators: $(gh api "repos/$repo/collaborators" --jq '[.[].login]|join(",")')"
