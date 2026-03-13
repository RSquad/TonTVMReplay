#!/bin/bash

set -e

# 0. Create reports directory if it doesn't exist
mkdir -p reports

# 1. Get the latest block number
get_seqno() {
    curl -s -X 'GET' 'https://toncenter.com/api/v2/getMasterchainInfo' -H 'accept: application/json' | jq -r .result.last.seqno
}

# 2. Update .env with new TO_SEQNO and FROM_SEQNO
update_env() {
    local TO_SEQNO="$1"
    local FROM_SEQNO="$2"
    # Remove old TO_SEQNO and FROM_SEQNO lines
    grep -vE '^(export )?(TO_SEQNO|FROM_SEQNO)=' .env > .env.tmp || true
    echo "export TO_SEQNO=$TO_SEQNO" >> .env.tmp
    echo "export FROM_SEQNO=$FROM_SEQNO" >> .env.tmp
    mv .env.tmp .env
}

# Initial setup
TO_SEQNO=$(get_seqno)
FROM_SEQNO=$((TO_SEQNO-1000))
update_env "$TO_SEQNO" "$FROM_SEQNO"

while true; do
    # 1. Source .env
    source .env

    # 2. Run main script
    ./run.sh

    # 3. Archive failed results if present
    archive_needed=false
    if [ -f failed_txs_pretty.json ]; then
        archive_needed=true
    fi
    if [ -d debug_dumps ] && [ "$(ls -A debug_dumps)" ]; then
        archive_needed=true
    fi
    if [ "$archive_needed" = true ]; then
        zip -r "reports/report_${FROM_SEQNO}_${TO_SEQNO}.zip" failed_txs_pretty.json debug_dumps 2>/dev/null || true
    fi

    # 4. Get new block number
    NEW_SEQNO=$(get_seqno)

    # 5. Check if we should move to next range
    if [ "$NEW_SEQNO" -ge $((TO_SEQNO+1000)) ]; then
        FROM_SEQNO=$TO_SEQNO
        TO_SEQNO=$((TO_SEQNO+1000))
        update_env "$TO_SEQNO" "$FROM_SEQNO"
    else
        sleep 60
    fi
    # 6. Repeat

done
