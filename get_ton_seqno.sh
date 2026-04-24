#!/bin/bash

# Script to get TON block seqno from a date/time string
# Usage: ./get_ton_seqno.sh "2026-04-07 12"

if [ -z "$1" ]; then
    echo "Usage: $0 \"YYYY-MM-DD HH\""
    echo "Example: $0 \"2026-04-07 12\""
    exit 1
fi

# Convert date string to Unix timestamp
# Input format: "YYYY-MM-DD HH"
UNIXTIME=$(date -d "$1:00:00 UTC" +%s 2>/dev/null)

# Fallback for macOS (which uses different date syntax)
if [ -z "$UNIXTIME" ] || [ "$UNIXTIME" = "-1" ]; then
    UNIXTIME=$(date -j -f "%Y-%m-%d %H:%M:%S" -u "$1:00:00" +%s 2>/dev/null)
fi

# Check if conversion was successful
if [ -z "$UNIXTIME" ] || [ "$UNIXTIME" = "-1" ]; then
    echo "Error: Invalid date format. Use YYYY-MM-DD HH (e.g., 2026-04-07 12)"
    exit 1
fi

# Query TON Center API and extract seqno
SEQNO=$(curl -s "https://toncenter.com/api/v2/lookupBlock?workchain=-1&shard=-9223372036854775808&unixtime=$UNIXTIME" | jq -r '.result.seqno')

# Check if API call was successful
if [ "$SEQNO" = "null" ] || [ -z "$SEQNO" ]; then
    echo "Error: Failed to get seqno from API"
    exit 1
fi

echo "$SEQNO"
