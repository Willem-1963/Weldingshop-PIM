#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
MESSAGE="${1:-}"

[[ -n "$MESSAGE" ]] || { echo "Gebruik: scripts/work-session-end.sh 'beschrijving'" >&2; exit 2; }
.venv/bin/python -m pytest -q

if [[ -z "$(git status --porcelain)" ]]; then
  echo "Geen wijzigingen om af te sluiten."
  exit 0
fi

git add -A
git commit -m "$MESSAGE"
git pull --rebase origin main
git push origin main
echo "Sessie veilig afgesloten en gedeeld: $(git rev-parse --short HEAD)"
