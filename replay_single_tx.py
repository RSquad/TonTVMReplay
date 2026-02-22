#!/usr/bin/env python3
"""
Replay a single transaction from a debug dump folder.
Useful for debugging after compare_rust_emulators.py found a discrepancy.

Usage:
    python replay_single_tx.py \
        --dump-dir ./debug_dumps/run_20260219_123456/failed/ABCD1234 \
        --emulator ./libemulator_rust.dylib \
        [--verbose]
"""
import argparse
import json
import os
from typing import Any, Dict, Optional

from loguru import logger
from tonpy import Cell, VmDict
from tonpy.autogen.block import Transaction, ShardAccount
from tonpy.tvm.not_native.emulator_extern import EmulatorExtern

from tonemuso.utils import normalize_prev_blocks_info

def _load_boc(path: str) -> Optional[Cell]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            boc_bytes = f.read()
        return Cell(boc_bytes.hex())
    except Exception as e:
        logger.warning(f"Failed to load BOC from {path}: {e}")
        return None


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load JSON from {path}: {e}")
        return None


def _parse_tx(cell: Cell) -> Dict[str, Any]:
    """Extract basic info from a transaction cell."""
    try:
        tx = Transaction().cell_unpack(cell, True)
        return {
            "hash": cell.get_hash(),
            "lt": tx.lt,
            "account_addr": tx.account_addr,
            "outmsg_cnt": tx.outmsg_cnt,
        }
    except Exception as e:
        return {"hash": cell.get_hash(), "parse_error": str(e)}


def _parse_shard_account(cell: Cell) -> Dict[str, Any]:
    """Extract basic info from a ShardAccount cell."""
    try:
        sa = ShardAccount().cell_unpack(cell, True)
        balance = None
        last_lt = None
        try:
            balance = sa.account.storage.balance.grams.amount.value
        except:
            pass
        try:
            last_lt = sa.last_trans_lt
        except:
            pass
        return {
            "hash": cell.get_hash(),
            "balance": balance,
            "last_trans_lt": last_lt,
        }
    except Exception as e:
        return {"hash": cell.get_hash(), "parse_error": str(e)}


def replay(dump_dir: str, emulator_path: str, verbose: bool = False) -> Dict[str, Any]:
    """Replay a transaction and return comparison results."""
    
    account_before = _load_boc(os.path.join(dump_dir, "account_before.boc"))
    in_msg = _load_boc(os.path.join(dump_dir, "in_msg.boc"))
    expected_tx = _load_boc(os.path.join(dump_dir, "expected_tx.boc"))
    config_cell = _load_boc(os.path.join(dump_dir, "config.boc"))
    libs_cell = _load_boc(os.path.join(dump_dir, "libs.boc"))
    
    error_info = _load_json(os.path.join(dump_dir, "error_info.json")) or {}
    block_info = _load_json(os.path.join(dump_dir, "block_info.json")) or {}
    prev_blocks = _load_json(os.path.join(dump_dir, "prev_blocks.json")) or []
    prev_blocks = normalize_prev_blocks_info(prev_blocks)

    if account_before is None:
        raise RuntimeError("account_before.boc not found")
    if config_cell is None:
        raise RuntimeError("config.boc not found")
    if expected_tx is None:
        raise RuntimeError("expected_tx.boc not found")

    lt = error_info.get("lt", 0)
    now = error_info.get("now", 0)
    is_tock = error_info.get("is_tock", False)
    rand_seed = error_info.get("rand_seed", "0" * 64)
    tx_hash = error_info.get("tx_hash", "UNKNOWN")

    config = VmDict(32, False, config_cell)
    
    em = EmulatorExtern(emulator_path, config)
    em.set_rand_seed(rand_seed)
    if prev_blocks:
        em.set_prev_blocks_info(prev_blocks)
    if libs_cell:
        em.set_libs(VmDict(256, False, cell_root=libs_cell))

    logger.info(f"Replaying tx: {tx_hash}")
    logger.info(f"  Block: wc={block_info.get('workchain')}, shard={block_info.get('shard')}, seqno={block_info.get('seqno')}")
    logger.info(f"  LT: {lt}, NOW: {now}, is_tock: {is_tock}")
    logger.info(f"  Emulator: {emulator_path}")
    
    if verbose:
        logger.info(f"  Account before: {_parse_shard_account(account_before)}")
        logger.info(f"  Expected tx: {_parse_tx(expected_tx)}")

    success = False
    try:
        if in_msg is None:
            success = em.emulate_tick_tock_transaction(account_before, is_tock, now, lt)
        else:
            success = em.emulate_transaction(account_before, in_msg, now, lt)
    except Exception as e:
        logger.error(f"Emulation exception: {e}")
        return {
            "success": False,
            "error": str(e),
            "expected_hash": expected_tx.get_hash() if expected_tx else None,
        }

    result_tx = em.transaction.to_cell() if em.transaction else None
    result_account = em.account.to_cell() if em.account else None
    
    expected_hash = expected_tx.get_hash()
    result_hash = result_tx.get_hash() if result_tx else None
    matches = result_hash == expected_hash

    logger.info(f"  Emulation success: {success}")
    logger.info(f"  Result tx hash: {result_hash}")
    logger.info(f"  Expected hash:  {expected_hash}")
    logger.info(f"  MATCH: {'YES' if matches else 'NO'}")

    if verbose and result_tx:
        logger.info(f"  Result tx: {_parse_tx(result_tx)}")
    if verbose and result_account:
        logger.info(f"  Account after: {_parse_shard_account(result_account)}")

    return {
        "success": success,
        "matches": matches,
        "expected_hash": expected_hash,
        "result_hash": result_hash,
        "result_tx_boc": result_tx.to_boc() if result_tx else None,
        "result_account_boc": result_account.to_boc() if result_account else None,
    }


def main():
    ap = argparse.ArgumentParser(description="Replay a single transaction from debug dump")
    ap.add_argument("--dump-dir", required=True, help="Path to debug dump folder")
    ap.add_argument("--emulator", required=True, help="Path to emulator .so/.dylib")
    ap.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    ap.add_argument("--save-result", help="Save result tx BOC to this path")
    args = ap.parse_args()

    result = replay(args.dump_dir, args.emulator, args.verbose)
    
    if args.save_result and result.get("result_tx_boc"):
        boc_bytes = bytes.fromhex(result["result_tx_boc"])
        with open(args.save_result, "wb") as f:
            f.write(boc_bytes)
        logger.info(f"Saved result tx to {args.save_result}")

    return 0 if result.get("matches") else 1


if __name__ == "__main__":
    raise SystemExit(main())
