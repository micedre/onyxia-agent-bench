#!/usr/bin/env bash
# Install nbstripout as a Git filter for the current repository, so notebook
# outputs are stripped at commit time (clean diffs, no data leaking into Git).
#
# Usage: run from the root of a Git repository:
#   ./setup-nbstripout.sh
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "✗ Not inside a Git repository — run this from your project root." >&2
  exit 1
fi

# Prefer uv (standard on Onyxia images), fall back to pipx then pip --user
if command -v uv >/dev/null 2>&1; then
  uv tool install nbstripout >/dev/null 2>&1 || true
  NBSTRIPOUT="uv tool run nbstripout"
elif command -v pipx >/dev/null 2>&1; then
  pipx install nbstripout >/dev/null 2>&1 || true
  NBSTRIPOUT="pipx run nbstripout"
else
  python3 -m pip install --user --quiet nbstripout
  NBSTRIPOUT="python3 -m nbstripout"
fi

$NBSTRIPOUT --install
echo "✓ nbstripout installed as a Git filter for this repository."
echo "  Notebook outputs will be stripped from commits (working files untouched)."
echo "  Verify with: git config filter.nbstripout.clean"
