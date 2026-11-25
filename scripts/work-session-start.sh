#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "STOP: lokale wijzigingen aanwezig. Rond de vorige sessie eerst af." >&2
  git status --short
  exit 2
fi

git fetch origin
git pull --ff-only origin main

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "Werkmap actueel: $(git rev-parse --short HEAD)"
echo "Start Codex vanuit: $ROOT"
