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

mkdir -p runs/reject_blocks

# Keep this as ordinary liteserver-base replay, but avoid noisy diagnostics.
export TONCENTER_TRACES_BY_MASTERS=FALSE
export TONPY_TX_DEBUG_LOG=0
export EMUSO_LOGLEVEL=2
export GET_ACCOUNT_STATE_PREFER_MC=1

# label                           from_mc   to_mc
ranges=(
  "70777933                       65897292  65897296"
  "70808466                       65928092  65928096"
  "70822553                       65942383  65942387"
  "70822639                       65942467  65942471"
  "70823001                       65942820  65942824"
  "70870537                       65990430  65990434"
  "70914397                       66034094  66034098"
  "71060064                       66180522  66180526"
  "71092931_71093304              66213077  66213449"
)

for item in "${ranges[@]}"; do
  read -r label from_seqno to_seqno <<< "$item"

  export FROM_SEQNO="$from_seqno"
  export TO_SEQNO="$to_seqno"
  export DEBUG_DUMPS_DIR="./debug_dumps/reject_${label}"

  rm -rf "$DEBUG_DUMPS_DIR"
  mkdir -p "$DEBUG_DUMPS_DIR"
  rm -f failed_txs.json failed_txs_pretty.json failed_txs_single.json failed_traces.json failed_traces_summary.json warnings.json emulation_report.html tonemuso_run.log

  log_path="runs/reject_blocks/${label}_${from_seqno}_${to_seqno}.log"
  status_path="runs/reject_blocks/${label}_${from_seqno}_${to_seqno}.status"

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start label=${label} FROM_SEQNO=${FROM_SEQNO} TO_SEQNO=${TO_SEQNO}"
  tonemuso > "$log_path" 2>&1
  status=$?
  echo "$status" > "$status_path"
  cp "$log_path" tonemuso_run.log

  for f in failed_txs.json failed_txs_pretty.json failed_txs_single.json failed_traces.json failed_traces_summary.json warnings.json emulation_report.html; do
    if [ -f "$f" ]; then
      cp "$f" "runs/reject_blocks/${label}_${f}"
    fi
  done

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] done label=${label} status=${status} log=${log_path}"
done
