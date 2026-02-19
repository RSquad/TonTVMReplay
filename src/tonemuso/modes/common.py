# Shared helpers extracted from main.py to avoid duplication
from collections import OrderedDict, defaultdict
import json
import os
from queue import Empty as QueueEmpty
from typing import Any, Dict, List, Optional, Set, Tuple

from tonpy.blockscanner.blockscanner import *
from tonpy import begin_cell
from tonpy.autogen.block import Transaction
from tonpy.tvm.not_native.emulator_extern import EmulatorExtern
from tonpy import Address
from loguru import logger
from tqdm import tqdm

from tonemuso.utils import b64_to_hex
from tonemuso.diff import make_json_dumpable
from tonemuso.emulation import TxStepEmulator, init_emulators, set_emulator_verbosity, _get_env_int, _create_emulator
from tonemuso.trace_models import TxRecord
from tonemuso.trace_runner import TraceOrderedRunner
from tonemuso.debug_dumper import get_dumper, init_dumper


# No module-level globals; helpers are parameterized by cfg-derived values.


def _dump_prev_blocks(block: Dict[str, Any], dump_dir: Optional[str]) -> None:
    log_enabled = _get_env_int("EMULATOR_PREV_BLOCKS_DUMP_LOG", 0) > 0
    blk = block.get('block_id')
    if blk is None:
        if log_enabled:
            logger.warning("prev_blocks dump skipped: block_id missing")
        return

    try:
        wc = blk.id.workchain
        shard = blk.id.shard
        seqno = blk.id.seqno
    except Exception:
        if log_enabled:
            logger.warning("prev_blocks dump skipped: failed to read block_id fields")
        return

    if not dump_dir:
        if log_enabled:
            logger.warning(
                f"prev_blocks dump skipped: EMULATOR_PREV_BLOCKS_DUMP_DIR is empty "
                f"for block wc={wc} sh={hex(shard).upper()[2:]} seq={seqno}"
            )
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
    except Exception:
        if log_enabled:
            logger.warning(f"prev_blocks dump skipped: failed to create dir {dump_dir!r}")
        return

    shard_hex = hex(shard).upper()[2:]
    file_name = f"wc{wc}_sh{shard_hex}_seq{seqno}_prev_blocks.json"
    file_path = os.path.join(dump_dir, file_name)
    if os.path.exists(file_path):
        try:
            if os.path.getsize(file_path) > 0:
                return
            logger.warning(f"prev_blocks dump file is empty, will overwrite: {file_path}")
        except Exception:
            return

    prev = block.get('prev_block_data') or []
    prev_100 = prev[0] if len(prev) > 0 else None
    prev_16 = prev[1] if len(prev) > 1 else None
    key_block_data = prev[2] if len(prev) > 2 else None

    key_block = block.get('key_block')
    key_block_id = None
    try:
        if isinstance(key_block, dict) and key_block.get('blk_id') is not None:
            key_block_id = key_block['blk_id'].to_data()
    except Exception:
        key_block_id = None

    def _blk_to_data(x: Any) -> Any:
        try:
            return x.to_data()
        except Exception:
            return None

    # Log gaps if any of the prev blocks are missing
    try:
        missing_16 = []
        if isinstance(prev_16, list):
            for i, v in enumerate(prev_16):
                if not v:
                    missing_16.append(i)
        missing_100 = []
        if isinstance(prev_100, list):
            for i, v in enumerate(prev_100):
                if not v:
                    missing_100.append(i)
        if prev_16 is None or prev_100 is None:
            logger.warning(
                f"prev_block_data missing for block wc={wc} sh={shard_hex} seq={seqno}: "
                f"prev_16={'None' if prev_16 is None else 'len='+str(len(prev_16))} "
                f"prev_100={'None' if prev_100 is None else 'len='+str(len(prev_100))}"
            )
        elif missing_16 or missing_100 or len(prev_16) < 16 or len(prev_100) < 16:
            logger.warning(
                f"prev_block_data gaps for block wc={wc} sh={shard_hex} seq={seqno}: "
                f"prev_16_len={len(prev_16)} missing_16={missing_16} "
                f"prev_100_len={len(prev_100)} missing_100={missing_100}"
            )
    except Exception:
        pass

    payload = {
        "block_id": _blk_to_data(blk),
        "key_block_id": key_block_id,
        "prev_block_left": _blk_to_data(block.get('prev_block_left')),
        "prev_block_right": _blk_to_data(block.get('prev_block_right')),
        "prev_blocks_100": prev_100,
        "prev_blocks_16": prev_16,
        "key_block_data": key_block_data,
    }

    try:
        payload = make_json_dumpable(payload)
        tmp_path = f"{file_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, ensure_ascii=True)
        os.replace(tmp_path, file_path)
        if _get_env_int("EMULATOR_PREV_BLOCKS_DUMP_LOG", 0) > 0:
            logger.info(
                f"prev_blocks dumped: {file_path} "
                f"prev_16_len={len(prev_16) if isinstance(prev_16, list) else 'None'} "
                f"prev_100_len={len(prev_100) if isinstance(prev_100, list) else 'None'}"
            )
    except Exception:
        try:
            if os.path.exists(f"{file_path}.tmp"):
                os.remove(f"{file_path}.tmp")
        except Exception:
            pass
        return


@curry
def process_blocks(data, config_override: dict = None, trace_whitelist: set = None, loglevel: int = 1,
                   color_schema: Optional[Dict[str, Any]] = None, emulator_path: Optional[str] = None,
                   emulator_unchanged_path: Optional[str] = None, txs_whitelist: Optional[Set[str]] = None,
                   debug_dumps_run_dir: Optional[str] = None):
    # Init dumper in worker process if not already initialized
    if debug_dumps_run_dir and get_dumper() is None:
        init_dumper(None, run_dir=debug_dumps_run_dir)
    
    out = []
    block, initial_account_state, txs = data

    _dump_prev_blocks(block, os.getenv("EMULATOR_PREV_BLOCKS_DUMP_DIR", "").strip())

    # Base/working configs
    base_config: VmDict = VmDict(32, False, block['key_block']['config'])
    config: VmDict = VmDict(32, False, block['key_block']['config'])
    if config_override is not None:
        for param in config_override:
            config.set(int(param), begin_cell().store_ref(Cell(config_override[param])).end_cell().begin_parse())

    # Emulators
    vm_log_verbosity = _get_env_int("EMULATOR_VM_LOG_VERBOSITY", 0)
    em = _create_emulator(emulator_path, config, vm_log_verbosity)
    set_emulator_verbosity(em, env_name="EMULATOR_VERBOSITY", default_level=1)
    em.set_rand_seed(block['rand_seed'])
    prev_block_data = [list(reversed(block['prev_block_data'][1])), block['prev_block_data'][2],
                       list(reversed(block['prev_block_data'][0]))]
    em.set_prev_blocks_info(prev_block_data)
    em.set_libs(VmDict(256, False, cell_root=Cell(block['libs'])))

    em2 = _create_emulator(emulator_unchanged_path, base_config, vm_log_verbosity)
    set_emulator_verbosity(em2, env_name="EMULATOR_UNCHANGED_VERBOSITY", default_level=1)
    em2.set_rand_seed(block['rand_seed'])
    em2.set_prev_blocks_info(prev_block_data)
    em2.set_libs(VmDict(256, False, cell_root=Cell(block['libs'])))

    # Filtering
    effective_filter = trace_whitelist or txs_whitelist
    if effective_filter is not None:
        process_this_chunk = any(tx['tx'].get_hash() in effective_filter for tx in txs)
        if not process_this_chunk:
            return []

    # Iterate
    account_state_em1 = initial_account_state
    account_state_em2 = initial_account_state
    step = TxStepEmulator(block=block, loglevel=loglevel, color_schema=color_schema, em=em,
                          account_state_em1=account_state_em1, em2=em2, account_state_em2=account_state_em2)
    for tx in txs:
        try:
            if txs_whitelist is not None and tx['tx'].get_hash() not in txs_whitelist:
                _out, account_state_em1, _ns2, _om = step.emulate(tx, extract_out_msgs=False)
                account_state_em2 = _ns2 if _ns2 is not None else account_state_em2
                continue
            tmp_out, account_state_em1, _ns2, _om = step.emulate(tx, extract_out_msgs=False)
            account_state_em2 = _ns2 if _ns2 is not None else account_state_em2
            out.extend(tmp_out)
        except Exception as e:
            logger.error(f"EMULATOR ERROR: Got {e} while emulating transaction! Continuing with next transaction...")
            # Continue processing remaining transactions instead of crashing the worker
            continue
    return out


@curry
def collect_raw(data, trace_tx_hashes_hex: Set[str], config_override: dict = None, loglevel: int = 1, emulator_path: Optional[str] = None,
                emulator_unchanged_path: Optional[str] = None):
    block, initial_account_state, txs = data

    # Build TxRecord objects and, if a non-empty trace set is provided, attach non-trace preceding txs
    tx_objs: List[TxRecord] = []
    buffer: List[TxRecord] = []  # holds non-trace txs until the next in-trace tx
    use_buffer = bool(trace_tx_hashes_hex) and len(trace_tx_hashes_hex) > 0
    for t in tqdm(txs, desc="Pre-emulate data"):
        if isinstance(t, dict):
            rec = TxRecord(tx=t['tx'], lt=t['lt'], now=t['now'], is_tock=t['is_tock'])
        else:
            rec = t
        if use_buffer:
            if rec.tx.get_hash().upper() in trace_tx_hashes_hex:
                rec.before_txs = list(buffer)
                buffer.clear()
            else:
                buffer.append(rec)
        tx_objs.append(rec)

    em, em2 = init_emulators(block, config_override, emulator_path=emulator_path,
                             emulator_unchanged_path=emulator_unchanged_path)

    account_state_em1 = initial_account_state
    account_state_em2 = initial_account_state

    step = TxStepEmulator(block=block, loglevel=loglevel, color_schema=None, em=em, account_state_em1=account_state_em1,
                          em2=em2, account_state_em2=account_state_em2)
    for tx in tx_objs:
        before_state_em2 = account_state_em2
        _out, account_state_em1, _ns2, _ = step.emulate(tx, extract_out_msgs=False)
        account_state_em2 = _ns2 if _ns2 is not None else account_state_em2
        tx.before_state_em2 = before_state_em2
        tx.after_state_em2 = account_state_em2
        tx.unchanged_emulator_tx_hash = em2.transaction.get_hash() if em2.transaction is not None else None

    return [(block, initial_account_state, tx_objs)]


def process_result(outq, loglevel: int = 1):
    total_txs = []
    while True:
        try:
            total_txs.append(outq.get_nowait())
        except QueueEmpty:
            break

    tmp_s = 0
    tmp_w = []
    tmp_u = []
    tmp_addrs = set()
    if len(total_txs) > 0:
        for chunk in total_txs:
            # Check for exceptions first
            if isinstance(chunk, Exception):
                logger.error(f"Worker error: {chunk}")
                continue
            # Skip if chunk is not iterable (e.g., an exception object)
            if not isinstance(chunk, (list, tuple)):
                logger.error(f"Unexpected worker output type: {type(chunk)} value={chunk}")
                continue
            for i in chunk:
                # Track unique addresses
                if 'address' in i:
                    tmp_addrs.add(i['address'])
                
                if i['mode'] == 'success':
                    tmp_s += 1
                elif i['mode'] == 'warning':
                    tmp_w.append(i)
                else:
                    tmp_u.append(i)

    if loglevel > 1 and (tmp_s or tmp_w or tmp_u):
        logger.warning(f"Emulator status: {tmp_s} success, {len(tmp_w)} warnings, {len(tmp_u)} errors")

    return tmp_s, tmp_u, tmp_w, tmp_addrs


# Worker helpers for multi-trace mode
_W_PREINDEX = None
_W_LCPARAMS = None
_W_LOGLEVEL = None
_W_COLOR_SCHEMA = None
_W_C7_ENV = None
_W_EMULATOR_PATH = None
_W_EMULATOR_UNCHANGED_PATH = None


def worker_init(preindexed, lcparams, loglevel, color_schema, c7_env, emulator_path, emulator_unchanged_path):
    global _W_PREINDEX, _W_LCPARAMS, _W_LOGLEVEL, _W_COLOR_SCHEMA, _W_C7_ENV, _W_EMULATOR_PATH, _W_EMULATOR_UNCHANGED_PATH
    _W_PREINDEX = preindexed
    _W_LCPARAMS = lcparams
    _W_LOGLEVEL = loglevel
    _W_COLOR_SCHEMA = color_schema
    _W_C7_ENV = c7_env
    _W_EMULATOR_PATH = emulator_path
    _W_EMULATOR_UNCHANGED_PATH = emulator_unchanged_path


def process_one_trace_worker(args):
    try:
        tidx, t = args
        config_override = json.loads(_W_C7_ENV) if _W_C7_ENV else None
        # t must be a TonTrace instance
        tx_order = t.transactions_order_b64
        tx_order_list = [b64_to_hex(h).upper() for h in (tx_order or [])]
        runner_local = TraceOrderedRunner(
            raw_chunks=None,
            config_override=config_override,
            loglevel=_W_LOGLEVEL,
            color_schema=_W_COLOR_SCHEMA,
            tx_order_hex_upper=tx_order_list or [],
            toncenter_trace=t,
            lcparams=_W_LCPARAMS,
            preindexed=_W_PREINDEX,
            emulator_path=_W_EMULATOR_PATH,
            emulator_unchanged_path=_W_EMULATOR_UNCHANGED_PATH
        )
        runner_local.run(tx_order_list or [])
        return runner_local.failed_traces or []
    except Exception as e:
        logger.error(f"Error processing trace #{args[0]} in worker: {e}")
        return []


def build_preindex(raw_chunks):
    blocks = {}
    tx_index = {}
    before_states = {}
    default_initial_state = defaultdict(OrderedDict)
    for block, initial_account_state, txs in raw_chunks:
        blk = block['block_id']
        block_key = (blk.id.workchain, blk.id.shard, blk.id.seqno, blk.root_hash)
        blocks[block_key] = block
        for tx in txs:
            try:
                txh = tx.tx.get_hash().upper()
                tx_index[txh] = (block_key, tx)
                tx_tlb = Transaction().cell_unpack(tx.tx, True)
                account_address = int(tx_tlb.account_addr, 2)
                account_addr = Address(f"{block_key[0]}:{hex(account_address).upper()[2:].zfill(64)}")
                if account_addr not in default_initial_state[block_key]:
                    default_initial_state[block_key][account_addr] = initial_account_state
                before_states[txh] = tx.before_state_em2
            except Exception:
                pass
    return {
        'blocks': blocks,
        'tx_index': tx_index,
        'before_states': before_states,
        'default_initial_state': default_initial_state,
    }
