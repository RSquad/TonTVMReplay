# TonTVMReplay Runbook

**Folders**
- Default runs live in `runs/default/`.
- Single‑transaction runs live in `runs/single/<tx_hash>/`.
- Root `failed_txs.json` is a symlink to `runs/default/failed_txs.json` (latest default run).

**Default Run (range of MC blocks)**
- Use when you want to emulate everything in a block range.
- Requires valid `LITESERVER_*` and `EMULATOR_*` in `.env`.

Example:
```bash
# .env
FROM_SEQNO=57160550
TO_SEQNO=57160555
TO_EMULATE_MC_BLOCKS=1

# run
 tonemuso 2>&1 | tee runs/default/run_57160550_57160555.log
```
Output:
- `runs/default/failed_txs.json` (via symlink `failed_txs.json`)
- logs in `runs/default/`

**Whitelist Run (specific tx list)**
- Use when you want to emulate only listed txs (still pulls key‑block config via LS).
- Requires `TXS_TO_PROCESS_PATH`.

Example:
```bash
# .env
TXS_TO_PROCESS_PATH=./txs_to_process.json

# run
 tonemuso 2>&1 | tee runs/single/whitelist.log
```

**Single Tx Run (one tx only, no full range)**
- Use `single_tx_emulate.py` to emulate exactly one tx and write a dedicated failed file.

Example:
```bash
./.venv/bin/python single_tx_emulate.py \
  --tx 4C90C139A5736F34EA3EEF62F0B06431719913835EA5A1B9173F20B2EF711583 \
  --workchain 0 \
  --shard 9223372036854775808 \
  --seqno 61926502 \
  --root-hash F19E15E8A6E9EE5BA806582DD4E779C3168A514B057791A381E620A224C52A16 \
  --file-hash 287388335AF81B33FFB9F18EB838516B5C3DBB26FA634F80A64692635816C8FB \
  --out runs/single/4C90C139/failed_4C90C139.json \
  2>&1 | tee runs/single/4C90C139/single_tx.log
```

**Optional: resolve block fields via tonapi**
```bash
./.venv/bin/python single_tx_emulate.py \
  --tx <TX_HASH> --tonapi --out runs/single/<TX_HASH>/failed_<TX_HASH>.json \
  2>&1 | tee runs/single/<TX_HASH>/single_tx.log
```

**Report Builder**
- Build a compact report from local dumps and failed files.

Example:
```bash
./.venv/bin/python report_single_tx.py \
  --tx 4C90C139A5736F34EA3EEF62F0B06431719913835EA5A1B9173F20B2EF711583 \
  --addr 5D6595912014A95F20D91B614F0832694AD2A0EF54A1270EBAD0D8A862FADE82 \
  --out runs/single/4C90C139/report_4C90C139.md
```

**Dump/Debug Env (optional)**
Pre/post emulation dumps:
- `EMULATOR_PRECALL_DUMP_DIR=./dump/precall`
- `EMULATOR_POST_DUMP_DIR=./dump/post`
- `EMULATOR_ACCOUNT_FAIL_DUMP_DIR=./dump/account_fail`

Prev‑block dumps:
- `EMULATOR_PREV_BLOCKS_DUMP_DIR=./dump/prev_blocks`
- `EMULATOR_PREV_BLOCKS_DUMP_LOG=1`

VM logs:
- `EMULATOR_VM_LOG_PRINT=1`
- `EMULATOR_VM_LOG_MAX=50000`
- `EMULATOR_VM_LOG_VERBOSITY=2`

**Notes**
- If the run hangs at `Load key blocks`, the LS likely fails `get_config_all()` for the key‑block. Try another LS or higher `LITESERVER_TIMEOUT`.
