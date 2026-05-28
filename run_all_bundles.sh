#!/bin/bash

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "my_venv" ]; then
  echo "ERROR: my_venv not found"
  exit 1
fi

source my_venv/bin/activate

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

BUNDLES_DIR="${BUNDLES_DIR:-$SCRIPT_DIR/bundles}"
RUN_ID="$(date -u +%Y%m%d_%H%M%S)"
RUN_ROOT="${BUNDLE_REPLAY_RUN_DIR:-$SCRIPT_DIR/runs/bundle_replay/$RUN_ID}"
COMPARE="${BUNDLE_COMPARE_EMULATORS:-0}"
DEBUG_MODE="${DEBUG_DUMPS_MODE:-minimal}"
ONLY_NONEMPTY="${BUNDLE_ONLY_NONEMPTY:-0}"
SOURCE_RUN_DIR="${BUNDLE_SOURCE_RUN_DIR:-}"

if [ ! -d "$BUNDLES_DIR" ]; then
  echo "ERROR: bundles dir not found: $BUNDLES_DIR"
  exit 1
fi

mkdir -p "$RUN_ROOT"
mkdir -p "$RUN_ROOT/dumps"

INDEX_FILE="$RUN_ROOT/index.tsv"
printf "bundle\tstatus\tsummary\tlog\tdump_dir\n" > "$INDEX_FILE"

compare_args=()
case "${COMPARE,,}" in
  1|true|yes)
    compare_args+=(--compare-emulators)
    ;;
esac

case "${ONLY_NONEMPTY,,}" in
  1|true|yes)
    if [ -z "$SOURCE_RUN_DIR" ]; then
      latest_run="$(find "$SCRIPT_DIR/runs/bundle_replay" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
      SOURCE_RUN_DIR="$latest_run"
    fi
    if [ ! -d "$SOURCE_RUN_DIR" ]; then
      echo "ERROR: BUNDLE_SOURCE_RUN_DIR not found: $SOURCE_RUN_DIR"
      exit 1
    fi
    ;;
esac

bundle_has_txs() {
  local bundle_name="$1"
  local summary_path="$SOURCE_RUN_DIR/${bundle_name}.summary.json"

  if [ ! -f "$summary_path" ]; then
    return 1
  fi

  python3 - "$summary_path" <<'PY'
import json
import sys

with open(sys.argv[1], "r") as f:
    data = json.load(f)

raise SystemExit(0 if int(data.get("total_txs", 0)) > 0 else 1)
PY
}

total=0
ok=0
fail=0
skip=0

while IFS= read -r bundle_dir; do
  [ -d "$bundle_dir" ] || continue

  bundle_name="$(basename "$bundle_dir")"

  case "${ONLY_NONEMPTY,,}" in
    1|true|yes)
      if ! bundle_has_txs "$bundle_name"; then
        skip=$((skip + 1))
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] skip bundle=${bundle_name} reason=empty_or_missing_prior_summary"
        continue
      fi
      ;;
  esac

  total=$((total + 1))
  summary_path="$RUN_ROOT/${bundle_name}.summary.json"
  log_path="$RUN_ROOT/${bundle_name}.log"
  status_path="$RUN_ROOT/${bundle_name}.status"
  dump_dir="$RUN_ROOT/dumps/${bundle_name}"

  mkdir -p "$dump_dir"

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start bundle=${bundle_name}"

  DEBUG_DUMPS_DIR="$dump_dir" \
  DEBUG_DUMPS_MODE="$DEBUG_MODE" \
  python3 replay_bundle_block.py \
    --bundle-dir "$bundle_dir" \
    "${compare_args[@]}" \
    --summary-out "$summary_path" \
    > "$log_path" 2>&1

  status=$?
  printf "%s\n" "$status" > "$status_path"
  printf "%s\t%s\t%s\t%s\t%s\n" "$bundle_name" "$status" "$summary_path" "$log_path" "$dump_dir" >> "$INDEX_FILE"

  if [ "$status" -eq 0 ]; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
  fi

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] done bundle=${bundle_name} status=${status}"
done < <(find "$BUNDLES_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

echo
echo "Bundle replay finished"
echo "run_root=$RUN_ROOT"
echo "total=$total ok=$ok fail=$fail skip=$skip"
