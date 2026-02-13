#!/bin/bash

# Quick run script for TonTVMReplay
# Activates venv, loads .env, and runs tonemuso

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "my_venv" ]; then
    echo "ERROR: Virtual environment not found!"
    echo "Please run ./setup.sh first"
    exit 1
fi

# Activate virtual environment
echo "Activating virtual environment..."
source my_venv/bin/activate

# Load environment variables if .env exists
if [ -f ".env" ]; then
    echo "Loading environment variables from .env..."
    set -a  # Automatically export all variables
    source .env
    set +a
    echo "Environment loaded successfully"
    echo ""
else
    echo "WARNING: .env file not found!"
    echo "Please create .env with your configuration"
    echo ""
fi

# Run tonemuso
echo "Starting TonTVMReplay..."
echo "================================================"
tonemuso "$@"
