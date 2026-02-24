#!/bin/bash

# Quick run script for TonTVMReplay
# Activates venv, loads .env, and runs tonemuso

echo "Cleaning up old logs and warnings..."
rm -f warnings.json tonemuso_run.log

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

# 
if [ -f "requirements.txt" ]; then
    echo "Installing Python dependencies from requirements.txt..."
    pip install -r requirements.txt
    echo -e "${GREEN}✓ Python dependencies installed${NC}"
else
    echo -e "${YELLOW}Warning: requirements.txt not found${NC}"
fi

# Install package in development mode
if [ -f "setup.py" ]; then
    echo "Installing TonTVMReplay package..."
    pip install -e .
    echo -e "${GREEN}✓ TonTVMReplay package installed${NC}"
else
    echo -e "${YELLOW}Warning: setup.py not found${NC}"
fi


# Clean up previous run files (after loading .env for dump dir paths)
echo "Cleaning up previous run files..."
rm -f emulation_report.html
rm -f failed_txs.json
rm -f failed_txs_pretty.json
rm -f failed_txs_single.json
rm -f failed_traces.json
rm -f failed_traces_summary.json
rm -f trace.json
rm -f tonemuso_run.log
rm -f nohup.out

# Clean up dump directories if they exist and are set
if [ -n "$EMULATOR_PRECALL_DUMP_DIR" ] && [ -d "$EMULATOR_PRECALL_DUMP_DIR" ]; then
    echo "Cleaning EMULATOR_PRECALL_DUMP_DIR: $EMULATOR_PRECALL_DUMP_DIR"
    rm -rf "$EMULATOR_PRECALL_DUMP_DIR"/*
fi
if [ -n "$EMULATOR_POST_DUMP_DIR" ] && [ -d "$EMULATOR_POST_DUMP_DIR" ]; then
    echo "Cleaning EMULATOR_POST_DUMP_DIR: $EMULATOR_POST_DUMP_DIR"
    rm -rf "$EMULATOR_POST_DUMP_DIR"/*
fi
if [ -n "$EMULATOR_MASTER_PROOF_DUMP_DIR" ] && [ -d "$EMULATOR_MASTER_PROOF_DUMP_DIR" ]; then
    echo "Cleaning EMULATOR_MASTER_PROOF_DUMP_DIR: $EMULATOR_MASTER_PROOF_DUMP_DIR"
    rm -rf "$EMULATOR_MASTER_PROOF_DUMP_DIR"/*
fi
if [ -n "$EMULATOR_ACCOUNT_FAIL_DUMP_DIR" ] && [ -d "$EMULATOR_ACCOUNT_FAIL_DUMP_DIR" ]; then
    echo "Cleaning EMULATOR_ACCOUNT_FAIL_DUMP_DIR: $EMULATOR_ACCOUNT_FAIL_DUMP_DIR"
    rm -rf "$EMULATOR_ACCOUNT_FAIL_DUMP_DIR"/*
fi
if [ -n "$EMULATOR_PREV_BLOCKS_DUMP_DIR" ] && [ -d "$EMULATOR_PREV_BLOCKS_DUMP_DIR" ]; then
    echo "Cleaning EMULATOR_PREV_BLOCKS_DUMP_DIR: $EMULATOR_PREV_BLOCKS_DUMP_DIR"
    rm -rf "$EMULATOR_PREV_BLOCKS_DUMP_DIR"/*
fi

echo "Cleanup complete"
echo ""

# Run tonemuso
echo "Starting TonTVMReplay..."
echo "================================================"

# Run tonemuso and capture output to log file
tonemuso "$@" 2>&1 | tee tonemuso_run.log

# Capture exit code
EXIT_CODE=$?

# Generate report if failed_txs.json exists
if [ -f "failed_txs.json" ] || [ -f "tonemuso_run.log" ]; then
    echo ""
    echo "================================================"
    echo "Generating emulation report..."
    echo "================================================"
    python3 generate_report.py
fi

# Exit with the original tonemuso exit code
exit $EXIT_CODE

