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

mkdir -p runs/collated_blocks

# Ordinary liteserver-base replay for selected collated shard-block windows.
export TONCENTER_TRACES_BY_MASTERS=FALSE
export TONPY_TX_DEBUG_LOG=0
export EMUSO_LOGLEVEL="${COLLATED_EMUSO_LOGLEVEL:-2}"
export GET_ACCOUNT_STATE_PREFER_MC=1

# label             from_mc   to_mc
ranges=(
  "70735584_70735587 65854499  65854505"
  "70735669_70735672 65854588  65854594"
  "70735748_70735750 65854675  65854680"
  "70735833_70735836 65854763  65854769"
  "70735921_70735924 65854851  65854857"
  "70736009_70736012 65854940  65854946"
  "70736096_70736099 65855029  65855035"
  "70761720_70761722 65880879  65880884"
)

for item in "${ranges[@]}"; do
  read -r label from_seqno to_seqno <<< "$item"

  export FROM_SEQNO="$from_seqno"
  export TO_SEQNO="$to_seqno"
  export DEBUG_DUMPS_DIR="./debug_dumps/collated_${label}"

  rm -rf "$DEBUG_DUMPS_DIR"
  mkdir -p "$DEBUG_DUMPS_DIR"
  rm -f failed_txs.json failed_txs_pretty.json failed_txs_single.json failed_traces.json failed_traces_summary.json warnings.json emulation_report.html tonemuso_run.log

  log_path="runs/collated_blocks/${label}_${from_seqno}_${to_seqno}.log"
  status_path="runs/collated_blocks/${label}_${from_seqno}_${to_seqno}.status"

  if [ -f "$status_path" ] && [ "$(cat "$status_path")" = "0" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] skip label=${label} status=0 log=${log_path}"
    continue
  fi

  timeout_sec="${COLLATED_WINDOW_TIMEOUT_SEC:-180}"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start label=${label} FROM_SEQNO=${FROM_SEQNO} TO_SEQNO=${TO_SEQNO} timeout=${timeout_sec}s"
  timeout -k 10s "${timeout_sec}s" tonemuso > "$log_path" 2>&1
  status=$?
  echo "$status" > "$status_path"
  cp "$log_path" tonemuso_run.log

  for f in failed_txs.json failed_txs_pretty.json failed_txs_single.json failed_traces.json failed_traces_summary.json warnings.json emulation_report.html; do
    if [ -f "$f" ]; then
      cp "$f" "runs/collated_blocks/${label}_${f}"
    fi
  done

  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] done label=${label} status=${status} log=${log_path}"
done
