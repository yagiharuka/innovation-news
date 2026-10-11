#!/usr/bin/env bash
# Persist only usage/failure checkpoints when collection or validation fails.
# Public outputs remain uncommitted until their existing health gates pass.
set -euo pipefail

# The article ledger and recovery manifest are not committed on validation
# failure. Preserve their durable queue counts, not uncommitted completion.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "${script_dir}/prepare_api_safety_checkpoint.py"

paths=()
for path in data/run_log.json data/review_state.json; do
  if [[ -f "$path" ]]; then
    paths+=("$path")
  fi
done
if (( ${#paths[@]} == 0 )); then
  echo "No API safety checkpoint is available to persist." >&2
  exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -- "${paths[@]}"
if git diff --cached --quiet -- "${paths[@]}"; then
  echo "No API safety changes to commit."
else
  git commit --only -m "data: persist API safety checkpoint" -- "${paths[@]}"
  git pull --rebase --autostash origin main
  git push
fi
