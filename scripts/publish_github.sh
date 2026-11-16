#!/usr/bin/env bash
# Explicit, user-run publishing only. Never force-push or overwrite an origin.
set -euo pipefail
owner="${1:-ecofree}"
repo="${2:-future-intelligence}"
[[ "$owner" =~ ^[A-Za-z0-9-]+$ && "$repo" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo 'Invalid owner or repository name.' >&2; exit 2;
}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"
for tool in git gh python; do
  command -v "$tool" >/dev/null || {
    echo "Missing $tool. Install it before running this script." >&2; exit 2;
  }
done
export GH_HOST=github.com
gh auth status --hostname github.com >/dev/null
actor="$(gh api --hostname github.com user --jq .login)"
[[ "$actor" == "$owner" ]] || {
  echo "Signed in as $actor, expected $owner. No repository created." >&2; exit 3;
}
full="$owner/$repo"
if gh repo view "$full" --json nameWithOwner >/dev/null 2>&1; then
  echo "$full already exists. Refusing to reuse or overwrite it." >&2
  exit 4
fi
# A failed existence lookup is not proof of absence. The create API below is
# authoritative and must succeed; network/authorization errors stop the script.
if [[ -e .git ]]; then
  [[ "$(git rev-parse --show-toplevel)" == "$root" ]] || exit 5
  if git remote get-url origin >/dev/null 2>&1; then
    echo 'An origin remote already exists. Refusing to change it.' >&2; exit 5
  fi
  [[ -z "$(git status --porcelain)" ]] || {
    echo 'Existing Git working tree is dirty. Review and commit it yourself first.' >&2; exit 5;
  }
  [[ "$(git branch --show-current)" == main ]] || {
    echo 'Existing branch is not main. Refusing to rename it.' >&2; exit 5;
  }
else
  if git rev-parse --show-toplevel >/dev/null 2>&1; then
    echo 'This folder is inside another repository. Extract it outside that repository.' >&2
    exit 5
  fi
  git init -b main
  if ! git config user.name >/dev/null; then git config --local user.name "$actor"; fi
  if ! git config user.email >/dev/null; then
    user_id="$(gh api --hostname github.com user --jq .id)"
    git config --local user.email "${user_id}+${actor}@users.noreply.github.com"
  fi
  git add -- .gitignore .env.example pyproject.toml README.md AGENTS.md docs future_intelligence tests scripts reports
  git commit -m 'Initialize Future Intelligence V2.0.1 specification and tested core'
fi
python -m unittest discover -s tests -v
# No public fallback and no retries that reuse an existing repository.
gh repo create "$full" --private --source . --remote origin --push \
  --description 'Professional future research and auditable probabilistic forecasting'
private="$(gh repo view "$full" --json isPrivate --jq .isPrivate)"
[[ "$private" == true ]] || {
  echo 'Repository visibility verification failed. Inspect GitHub immediately.' >&2; exit 6;
}
local_sha="$(git rev-parse HEAD)"
remote_sha="$(git ls-remote origin refs/heads/main | awk '{print $1}')"
[[ "$remote_sha" == "$local_sha" ]] || {
  echo 'Remote commit verification failed. No success claimed.' >&2; exit 7;
}
printf '\nVerified remote commit: %s\n' "$remote_sha"
gh repo view "$full" --json nameWithOwner,url,isPrivate,defaultBranchRef
