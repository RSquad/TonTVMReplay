# TonTVMReplay

A comprehensive TON blockchain transaction emulation and verification tool that replays transactions using the TVM (TON Virtual Machine) emulator to validate transaction execution against actual blockchain data.

## Overview

TonTVMReplay compares emulated transaction execution with real blockchain results to:
- Verify emulator correctness and blockchain consistency
- Debug smart contract behavior
- Analyze transaction traces and message flows
- Test configuration changes impact (via C7 overrides)
- Identify discrepancies between expected and actual execution

The tool uses **dual-emulator comparison**:
- **Primary Emulator**: With optional config overrides (C7_REWRITE)
- **Secondary Emulator**: Baseline/unchanged configuration

## Installation

### Prerequisites
- Python 3.8+
- Rust (for building Rust emulator) - [Install from rustup.rs](https://rustup.rs/)
- C++ build tools (for building C++ emulator): `cmake`, `g++`, `clang`
- Git with SSH access to GitHub (for Rust emulator)
- Access to a TON liteserver

### Quick Setup (Automated)

**Recommended**: Use the automated setup script that builds both emulators and sets up Python environment:

```bash
git clone https://github.com/disintar/TonTVMReplay.git
cd TonTVMReplay
./setup.sh
```

The script will:
1. ✅ Build **Rust emulator** from [RSquad/ton-node](https://github.com/RSquad/ton-node) (branch: tvm-emulator0)
2. ✅ Build **C++ emulator** from [ton-blockchain/ton](https://github.com/ton-blockchain/ton)
3. ✅ Create Python virtual environment and install dependencies
4. ✅ Install TonTVMReplay package

**Quick run** after setup:
```bash
./run.sh  # Automatically loads .env and runs tonemuso
```

### Manual Setup

If you prefer manual installation or already have emulator binaries:

1. Clone the repository:
```bash
git clone https://github.com/disintar/TonTVMReplay.git
cd TonTVMReplay
```

2. Place emulator libraries:
   - Rust emulator: `rust/libemulator.so`
   - C++ emulator: `cpp/libemulator.so`

3. Create virtual environment and install dependencies:
```bash
python3 -m venv my_venv
source my_venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

4. Configure environment variables in `.env` file (see [Configuration](#configuration))

5. Run:
```bash
source .env
tonemuso
# or use: ./run.sh
```

## Usage Scenarios

TonTVMReplay supports **4 operational modes**, automatically selected based on environment variables:

### Scenario 1: Single Trace Mode 🔍

**Use Case**: Analyze a single transaction trace with its complete execution tree

**When to Use**:
- Debugging specific transaction failures
- Understanding message flow for a particular transaction
- Verifying child transaction execution order

**Configuration**:
```bash
export TONCENTER_TX_HASH="<transaction_hash_hex_or_base64>"
# OR
export TONCENTER_MSG_HASH="<message_hash_hex_or_base64>"

# Optional:
export TONCENTER_API_KEY="<your_api_key>"  # For rate limit bypass
export TONCENTER_API="https://toncenter.com/api/v3"

# Required liteserver config:
export LITESERVER_SERVER="<ip_as_integer>"
export LITESERVER_PORT="<port>"
export LITESERVER_PUBKEY="<base64_pubkey>"
export EMULATOR_PATH="/path/to/emulator.so"
```

**Example**:
```bash
export TONCENTER_TX_HASH="9CE993000E4D40F81FBE867972E712ECD6D55849D8E0FC1F54A2ACF657BD9315"
export EMULATOR_PATH="./cpp/libemulator_cpp.so"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWLOXHYMvMcrGZDPs+pn58a17LFbnXoQkKc2xw="
tonemuso
```

**Output**:
- Tree visualization showing parent → child transaction relationships
- Per-transaction emulation status (success/warning/error)
- Diff analysis for mismatched transactions
- `failed_traces.json` with detailed failure information

**How It Works**:
1. Queries Toncenter API for the complete trace
2. Identifies all blocks containing trace transactions
3. Downloads only relevant blocks via liteserver
4. Emulates transactions in tree order (depth-first)
5. Compares emulated vs actual results

---

### Scenario 2: Multi-Trace Batch Mode 📊

**Use Case**: Bulk analysis of multiple traces within a masterchain time range

**When to Use**:
- Large-scale emulator validation across many transactions
- Statistical analysis of emulation accuracy
- Regression testing after emulator updates
- Identifying patterns in failing contracts

**Configuration**:
```bash
export TONCENTER_TRACES_BY_MASTERS="true"
export FROM_SEQNO="<start_masterchain_seqno>"  # Optional
export TO_SEQNO="<end_masterchain_seqno>"      # Optional, defaults to latest
export TO_EMULATE_MC_BLOCKS="10"               # Number of recent blocks if seqnos not set

# Performance tuning:
export NPROC="10"              # Number of parallel worker processes
export TX_CHUNK_SIZE="40000"   # Tune based on available RAM

# Liteserver config (same as Scenario 1)
```

**Example**:
```bash
export TONCENTER_TRACES_BY_MASTERS="true"
export FROM_SEQNO="45000000"
export TO_SEQNO="45000100"
export NPROC="20"
export EMULATOR_PATH="./cpp/libemulator_cpp.so"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWLOXHYMvMcrGZDPs+pn58a17LFbnXoQkKc2xw="
tonemuso
```

**Output**:
- Progress bars for downloading and emulation phases
- Aggregated statistics: success/warning/error counts across all traces
- `failed_traces.json` with all failing trace details
- Per-trace and per-transaction status summary

**How It Works**:
1. Resolves masterchain logical time (LT) range from seqnos
2. Fetches all traces from Toncenter within that LT range (paginated)
3. Builds union of all required blocks
4. Downloads all blocks in one efficient scan
5. Pre-indexes transactions and account states
6. Distributes traces across worker pool for parallel emulation
7. Aggregates results from all workers

**Performance Tips**:
- Use multiple liteservers for better throughput (see [Liteserver Failover](#liteserver-failover))
- Reduce `TX_CHUNK_SIZE` if running out of memory
- Increase `NPROC` for faster processing (watch CPU/RAM usage)

---

### Scenario 3: Whitelist Mode ✅

**Use Case**: Emulate specific transactions from a curated list

**When to Use**:
- Testing known problematic transactions
- Regression test suite with fixed transaction set
- Analyzing transactions from external sources (GraphQL queries)
- Focused debugging of specific contract interactions

**Configuration**:
```bash
export TXS_TO_PROCESS_PATH="/path/to/transactions.json"
export EMULATOR_PATH="/path/to/emulator.so"
# Liteserver config (same as above)
```

**Transaction List Format** (`transactions.json`):
```json
{
  "transactions": [
    {
      "hash": "<tx_hash_base64>",
      "lt": 123456789,
      "workchain": 0,
      "shard": "8000000000000000",
      "seqno": 12345678,
      "root_hash": "<block_root_hash_base64>",
      "file_hash": "<block_file_hash_base64>"
    },
    ...
  ]
}
```

**How to Get Transaction Data** (using TON GraphQL):
```graphql
{
  transactions(
    trace_hash: "9CE993000E4D40F81FBE867972E712ECD6D55849D8E0FC1F54A2ACF657BD9315"
    page_size: 150
  ) {
    hash
    lt
    workchain
    shard
    seqno
    root_hash
    file_hash
  }
}
```

**Example**:
```bash
export TXS_TO_PROCESS_PATH="./trace.json"
export EMULATOR_PATH="./cpp/libemulator_cpp.so"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWLOXHYMvMcrGZDPs+pn58a17LFbnXoQkKc2xw="
tonemuso
```

**Output**:
- Success/warning/error counts
- Address-level error statistics
- Top 5 most common failing contract addresses
- `failed_txs.json` with failure details

[Example JSON file](https://github.com/disintar/TonTVMReplay/blob/master/trace.json)

---

### Scenario 4: Liteserver Base Mode (Sequential Scan) 🔄

**Use Case**: Full blockchain scan and emulation of sequential blocks

**When to Use**:
- Continuous monitoring of recent blocks
- Comprehensive emulator validation across all transactions
- Analyzing blockchain behavior over time ranges
- Finding edge cases in real-world transaction data

**Configuration**:
```bash
# Option A: Scan specific seqno range
export FROM_SEQNO="45000000"
export TO_SEQNO="45000010"

# Option B: Scan recent blocks (default)
export TO_EMULATE_MC_BLOCKS="10"  # Last 10 masterchain blocks

# Optional filters:
export ONLYMC_BLOCK="true"        # Emulate only masterchain blocks
export PARSE_OVER_LS="true"       # Parse transactions via liteserver

# Performance tuning:
export NPROC="10"
export CHUNK_SIZE="2"             # Blocks per iteration
export TX_CHUNK_SIZE="40000"      # Transactions per batch

# Liteserver config (same as above)
```

**Example**: Scan last 50 blocks
```bash
export TO_EMULATE_MC_BLOCKS="50"
export NPROC="15"
export EMULATOR_PATH="./cpp/libemulator_cpp.so"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWLOXHYMvMcrGZDPs+pn58a17LFbnXoQkKc2xw="
tonemuso
```

**Output**:
- Real-time progress tracking (tqdm)
- Continuous success/warning/error statistics
- Address-level failure analysis
- `failed_txs.json` with all failures

**How It Works**:
1. Determines seqno range (explicit or from latest blocks)
2. Sequentially scans blocks using `BlockScanner`
3. Emulates ALL transactions in block order
4. Processes results in real-time as chunks complete

---

## Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `LITESERVER_SERVER` | Liteserver IP as integer | `1091897261` |
| `LITESERVER_PORT` | Liteserver port | `13206` |
| `LITESERVER_PUBKEY` | Ed25519 public key (base64) | `K0t3+IWLOXHYMvMc...` |
| `EMULATOR_PATH` | Path to emulator `.so` file | `./cpp/libemulator_cpp.so` |

### Optional Configuration

#### Liteserver Failover

For high availability, configure multiple liteservers with round-robin failover:

```bash
# Primary liteserver
export LITESERVER_SERVER_1="1091897261"
export LITESERVER_PORT_1="13206"
export LITESERVER_PUBKEY_1="K0t3+IWLOXHYMvMcrGZDPs+pn58a17LFbnXoQkKc2xw="

# Backup liteserver
export LITESERVER_SERVER_2="1091931027"
export LITESERVER_PORT_2="53312"
export LITESERVER_PUBKEY_2="wrQaeIFispPfHndEBc0s0fx7GSp8UFFvebnytQQfc6A="

# Add up to _99 servers
export LITESERVER_TIMEOUT="5"  # Timeout per attempt (seconds)
```

#### Performance Tuning

| Variable | Default | Description | Recommendation |
|----------|---------|-------------|----------------|
| `NPROC` | `10` | Parallel worker processes | Match CPU cores, watch RAM |
| `CHUNK_SIZE` | `2` | Blocks per iteration | Lower for less memory |
| `TX_CHUNK_SIZE` | `40000` | Transactions per batch | Reduce if <32GB RAM |
| `LITESERVER_TIMEOUT` | `5` | Liteserver request timeout (seconds) | Increase for slow networks |

#### Logging

| Variable | Value | Behavior |
|----------|-------|----------|
| `EMUSO_LOGLEVEL` | `0` | Disabled |
| | `1` | Per-chunk summary (default) |
| | `2` | Detailed with progress bars |

#### Advanced Options

| Variable | Description | Default |
|----------|-------------|---------|
| `FROM_SEQNO` | Start masterchain seqno | Latest - `TO_EMULATE_MC_BLOCKS` |
| `TO_SEQNO` | End masterchain seqno | Latest |
| `TO_EMULATE_MC_BLOCKS` | Recent blocks to process | `10` |
| `ONLYMC_BLOCK` | Emulate only masterchain | `false` |
| `PARSE_OVER_LS` | Parse txs via liteserver | `false` |
| `C7_REWRITE` | Override config params | `{"1": "base64_boc"}` |
| `COLOR_SCHEMA_PATH` | Path to color schema JSON | None |
| `EMULATOR_UNCHANGED_PATH` | Baseline emulator `.so` | Same as `EMULATOR_PATH` |
| `TONCENTER_API` | Toncenter API base URL | `https://toncenter.com/api/v3` |
| `TONCENTER_API_KEY` | API key for rate limits | None |

### Color Schema

Control diff validation severity levels via JSON file:

```json
{
  "transaction": {
    "account_addr": "skip",
    "lt": "warn",
    "total_fees": "alarm"
  },
  "account": {
    "balance": "alarm",
    "last_trans_lt": "skip"
  }
}
```

**Severity Levels**:
- `"skip"`: Ignore differences in this field
- `"warn"`: Log warning but count as success
- `"alarm"`: Count as error/unsuccess

**Usage**:
```bash
export COLOR_SCHEMA_PATH="./diff_colored.json"
```

[Example color schema](https://github.com/disintar/TonTVMReplay/blob/master/diff_colored.json)

### C7 Configuration Override

Test behavior under modified blockchain config:

```bash
export C7_REWRITE='{"1": "te6ccgEBAQEAAgAAAA=="}'  # Override config param 1
```

This allows testing "what-if" scenarios with different network parameters.

---

## Output Files

| File | Generated By | Content |
|------|--------------|---------|
| `failed_txs.json` | Whitelist, Liteserver Base | Transaction-level failures with diffs |
| `failed_traces.json` | Single/Multi-Trace modes | Complete trace trees with status annotations |
| `failed_traces_summary.json` | Manual (`print_failed_traces.py`) | Filtered non-success traces |

### Output Format Example

**failed_traces.json**:
```json
[
  {
    "final_status": "unsuccess",
    "emulated_trace": {
      "tx_hash": "...",
      "mode": "error",
      "children": [...],
      "diff": {...}
    },
    "original_trace": {...},
    "not_presented": [...]
  }
]
```

**Analyzing Results**:
```bash
python print_failed_traces.py  # Creates failed_traces_summary.json
```

---

## Validation Metrics

All scenarios report:
- ✅ **Success**: Perfect transaction hash match
- ⚠️ **Warning**: Minor diffs (per color schema)
- ❌ **Error**: Major mismatches between emulated and actual
- 🆕 **New transactions**: Emulated but not in original trace
- ❓ **Missed transactions**: Expected but not emulated

**Address-level statistics**: Identifies most problematic smart contracts

---

## Troubleshooting

### Common Issues

**Out of Memory**:
```bash
export TX_CHUNK_SIZE="20000"  # Reduce from default 40000
export NPROC="5"              # Reduce parallelism
```

**Liteserver Timeouts**:
```bash
export LITESERVER_TIMEOUT="10"  # Increase from default 5
# Configure multiple liteservers for failover
```

**No Emulator File**:
- Download or compile TON emulator library
- Set `EMULATOR_PATH` to absolute path

**Toncenter Rate Limits**:
```bash
export TONCENTER_API_KEY="your_key_here"  # Get from toncenter.com
```

---

## Examples

### Example 1: Debug Failed Transaction
```bash
export TONCENTER_TX_HASH="abc123..."
export EMULATOR_PATH="./emulator.so"
export COLOR_SCHEMA_PATH="./diff_colored.json"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWL..."
tonemuso
```

### Example 2: Validate Last 100 Blocks
```bash
export TO_EMULATE_MC_BLOCKS="100"
export NPROC="20"
export EMULATOR_PATH="./emulator.so"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWL..."
tonemuso
```

### Example 3: Batch Process Traces in Time Range
```bash
export TONCENTER_TRACES_BY_MASTERS="true"
export FROM_SEQNO="45000000"
export TO_SEQNO="45001000"
export TONCENTER_API_KEY="your_key"
export NPROC="30"
export EMULATOR_PATH="./emulator.so"
export LITESERVER_SERVER="1091897261"
export LITESERVER_PORT="13206"
export LITESERVER_PUBKEY="K0t3+IWL..."
tonemuso
```

---

## License

Apache License Version 2.0

## Contributing

Contributions welcome! Please open issues for bugs or feature requests.
