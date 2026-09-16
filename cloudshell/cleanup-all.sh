#!/usr/bin/env bash
#
# Thin wrapper around infrastructure/cleanup.py.
#
# All AWS deletion logic lives in infrastructure/cleanup.py — this script
# never reimplements it. It exists for the case where you have a full git
# checkout of the project (rather than only the pasted deploy-e2e script)
# and want one command that both deletes the AWS resources and removes the
# local artifacts the CloudShell deploy scripts create:
# ~/.novamart-state and the staged project copy at ~/novamart-project.
#
#     bash cloudshell/cleanup-all.sh
#
# The deploy-e2e-*.sh script's own --teardown flag does the same thing for a
# CloudShell session where only that one file was pasted in.

set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/novamart-project}"
STATE_DIR="${STATE_DIR:-$HOME/.novamart-state}"

# Prefer a checked-out infrastructure/cleanup.py next to this script over the
# staged copy at $PROJECT_DIR, so `bash cloudshell/cleanup-all.sh` works from
# a git clone even before any deploy script has run.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLEANUP_PY=""
if [[ -f "$HERE/infrastructure/cleanup.py" ]]; then
  CLEANUP_PY="$HERE/infrastructure/cleanup.py"
  RUN_DIR="$HERE"
elif [[ -f "$PROJECT_DIR/infrastructure/cleanup.py" ]]; then
  CLEANUP_PY="$PROJECT_DIR/infrastructure/cleanup.py"
  RUN_DIR="$PROJECT_DIR"
else
  echo "No infrastructure/cleanup.py found (checked $HERE and $PROJECT_DIR)." >&2
  echo "Nothing to clean up — run a deploy script or check out the repo first." >&2
  exit 1
fi

echo "Deleting AWS resources via ${CLEANUP_PY#$HERE/} --yes ..."
( cd "$RUN_DIR" && python3 infrastructure/cleanup.py --yes )
rc=$?

echo "Removing local artifacts: $STATE_DIR, $PROJECT_DIR"
rm -rf "$STATE_DIR" "$PROJECT_DIR"

if [[ $rc -ne 0 ]]; then
  echo "cleanup.py reported at least one failure — check its summary above" >&2
  echo "and finish any remaining deletions in the AWS console." >&2
fi
exit $rc
