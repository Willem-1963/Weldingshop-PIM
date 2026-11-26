#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
MESSAGE="${1:-}"

[[ -n "$MESSAGE" ]] || { echo "Gebruik: scripts/work-session-end.sh 'beschrijving'" >&2; exit 2; }
[[ "$(git branch --show-current)" == main ]] || { echo "Afgebroken: checkout eerst de branch main." >&2; exit 1; }
.venv/bin/python -m pytest -q

if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -m "$MESSAGE"
else
  echo "Geen nieuwe wijzigingen om te committen; synchronisatie wordt gecontroleerd."
fi

git pull --rebase origin main
git push origin main
git fetch origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse refs/remotes/origin/main)" ]] || { echo "Afgebroken: lokale en gedeelde commit verschillen." >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Afgebroken: er zijn nog lokale wijzigingen." >&2; exit 1; }
echo "Sessie veilig afgesloten en gedeeld: $(git rev-parse --short HEAD)"
