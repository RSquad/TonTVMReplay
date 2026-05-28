#!/bin/bash
set -o pipefail

# Run multiple tonemuso instances in parallel, each with its own
# FROM_SEQNO/TO_SEQNO range and isolated output directory.
#
# Usage:
#   ./multi_run.sh                  # reads ranges from runs.conf
#   ./multi_run.sh my_ranges.conf   # reads ranges from custom file
#
# Each run gets its own directory under runs/<timestamp>/<from>_<to>/
# with all logs, reports, and debug dumps inside.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${1:-$SCRIPT_DIR/runs.conf}"

if [ ! -f "$CONF" ]; then
    echo "ERROR: Config file not found: $CONF"
    echo "Create it with one range per line: FROM_SEQNO TO_SEQNO"
    exit 1
fi

# Read ranges (skip comments and blank lines)
RANGES=()
while IFS= read -r line; do
    line="${line%%#*}"          # strip comments
    line="$(echo "$line" | xargs)"  # trim whitespace
    [ -z "$line" ] && continue
    RANGES+=("$line")
done < "$CONF"

if [ ${#RANGES[@]} -eq 0 ]; then
    echo "ERROR: No ranges found in $CONF"
    exit 1
fi

echo "Found ${#RANGES[@]} range(s) to run"

# Create timestamped parent directory for this batch
BATCH_DIR="$SCRIPT_DIR/runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BATCH_DIR"
echo "Output directory: $BATCH_DIR"

# Activate venv
if [ ! -d "$SCRIPT_DIR/my_venv" ]; then
    echo "ERROR: Virtual environment not found! Run ./setup.sh first"
    exit 1
fi
source "$SCRIPT_DIR/my_venv/bin/activate"

# Load base .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Resolve relative paths from .env to absolute (relative to SCRIPT_DIR)
for var in COLOR_SCHEMA_PATH EMULATOR_PATH EMULATOR_UNCHANGED_PATH DEBUG_DUMPS_DIR; do
    val="${!var}"
    if [ -n "$val" ] && [[ "$val" != /* ]]; then
        export "$var=$SCRIPT_DIR/$val"
    fi
done

# Install deps once
pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null
pip install -q -e "$SCRIPT_DIR" 2>/dev/null

# Reduce NPROC per run to avoid overwhelming the liteserver
NPROC_PER_RUN=$(( ${NPROC:-10} / ${#RANGES[@]} ))
[ "$NPROC_PER_RUN" -lt 1 ] && NPROC_PER_RUN=1
echo "NPROC per run: $NPROC_PER_RUN (total NPROC=${NPROC:-10}, ranges=${#RANGES[@]})"

# Launch all ranges in parallel
PIDS=()
RUN_DIRS=()
LAUNCH_IDX=0

for range in "${RANGES[@]}"; do
    read -r FROM TO <<< "$range"
    RUN_DIR="$BATCH_DIR/${FROM}_${TO}"
    mkdir -p "$RUN_DIR"
    RUN_DIRS+=("$RUN_DIR")

    # Stagger launches so they don't all hit the liteserver at once
    if [ "$LAUNCH_IDX" -gt 0 ]; then
        echo "  (waiting 5s before next launch...)"
        sleep 5
    fi
    LAUNCH_IDX=$((LAUNCH_IDX + 1))

    echo "Starting range $FROM -> $TO  (dir: $RUN_DIR)"

    (
        cd "$RUN_DIR"

        # Override per-run env vars
        export FROM_SEQNO="$FROM"
        export TO_SEQNO="$TO"
        export NPROC="$NPROC_PER_RUN"
        export DEBUG_DUMPS_DIR="$RUN_DIR/debug_dumps"
        export EMULATOR_PRECALL_DUMP_DIR="$RUN_DIR/precall_dumps"
        export EMULATOR_POST_DUMP_DIR="$RUN_DIR/post_dumps"
        export EMULATOR_MASTER_PROOF_DUMP_DIR="$RUN_DIR/master_proof_dumps"
        export EMULATOR_ACCOUNT_FAIL_DUMP_DIR="$RUN_DIR/account_fail_dumps"
        export EMULATOR_PREV_BLOCKS_DUMP_DIR="$RUN_DIR/prev_blocks_dumps"

        # Save the effective config for this run
        echo "FROM_SEQNO=$FROM" > "$RUN_DIR/run_config.txt"
        echo "TO_SEQNO=$TO" >> "$RUN_DIR/run_config.txt"
        echo "Started: $(date -Iseconds)" >> "$RUN_DIR/run_config.txt"

        # Run tonemuso, capture log (PYTHONUNBUFFERED prevents pipe buffering stalls)
        PYTHONUNBUFFERED=1 tonemuso 2>&1 | tee "$RUN_DIR/tonemuso_run.log"
        EXIT_CODE=${PIPESTATUS[0]}

        echo "Exit code: $EXIT_CODE" >> "$RUN_DIR/run_config.txt"
        echo "Finished: $(date -Iseconds)" >> "$RUN_DIR/run_config.txt"

        # Generate report if applicable
        if [ -f "$RUN_DIR/failed_txs.json" ] || [ -f "$RUN_DIR/tonemuso_run.log" ]; then
            cd "$RUN_DIR"
            ln -sf "$RUN_DIR/tonemuso_run.log" tonemuso_run.log 2>/dev/null
            ln -sf "$RUN_DIR/failed_txs.json" failed_txs.json 2>/dev/null
            python3 "$SCRIPT_DIR/generate_report.py" 2>/dev/null
            rm -f tonemuso_run.log failed_txs.json 2>/dev/null
        fi

        exit $EXIT_CODE
    ) &

    PIDS+=($!)
done

echo ""
echo "All ${#RANGES[@]} runs launched. Waiting..."
echo ""

# Wait for all and collect results
FAILED=0
for i in "${!PIDS[@]}"; do
    pid=${PIDS[$i]}
    run_dir=${RUN_DIRS[$i]}
    range=${RANGES[$i]}

    if wait "$pid"; then
        echo "OK    $range  -> $run_dir"
    else
        echo "FAIL  $range  -> $run_dir"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "================================================"
echo "Batch complete: $BATCH_DIR"
echo "  Total:  ${#RANGES[@]}"
echo "  OK:     $(( ${#RANGES[@]} - FAILED ))"
echo "  Failed: $FAILED"
echo "================================================"

exit $FAILED
