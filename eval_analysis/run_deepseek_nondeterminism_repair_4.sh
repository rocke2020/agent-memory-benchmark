#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--confirm-supplier-versions" || "$#" -ne 1 ]]; then
  echo "Usage: $0 --confirm-supplier-versions" >&2
  echo "The flag confirms Flash 0731, Pro 0813, and default thinking high." >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STUDY_ID="deepseek-nondeterminism-20260822-55a"
LOG_PATH="$REPOSITORY_ROOT/run-artifacts/2028-0819->0822/longmemeval-hindsight-deepseek-nondeterminism-repair-4.log"

cd "$REPOSITORY_ROOT"
mkdir -p "$(dirname "$LOG_PATH")"
if ! (set -o noclobber; : > "$LOG_PATH") 2>/dev/null; then
  echo "Refusing to overwrite existing log: $LOG_PATH" >&2
  exit 2
fi

{
  uv run --locked python eval_analysis/deepseek_nondeterminism_repair4.py preflight \
    --study-id "$STUDY_ID" \
    --live \
    --confirm-supplier-versions
  uv run --locked python eval_analysis/deepseek_nondeterminism_repair4.py run \
    --study-id "$STUDY_ID"
  uv run --locked python eval_analysis/deepseek_nondeterminism_repair4.py finalize \
    --study-id "$STUDY_ID"
  uv run --locked python eval_analysis/deepseek_nondeterminism_repair4.py analyze \
    --study-id "$STUDY_ID"
} 2>&1 | tee -a "$LOG_PATH"
