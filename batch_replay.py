#!/usr/bin/env python3
"""
Batch replay all failed transactions from a debug run with a new emulator.
Useful to verify fixes.

Usage:
    python batch_replay.py \
        --run-dir ./debug_dumps/run_20260219_123456 \
        --emulator ./libemulator_rust_fixed.dylib \
        [--parallel 4]
"""
import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Tuple

from loguru import logger


def replay_one(args: Tuple[str, str]) -> Dict:
    """Replay a single transaction. Run in subprocess."""
    dump_dir, emulator_path = args
    
    from replay_single_tx import replay
    
    tx_hash = os.path.basename(dump_dir)
    try:
        result = replay(dump_dir, emulator_path, verbose=False)
        return {
            "tx_hash": tx_hash,
            "dump_dir": dump_dir,
            "success": result.get("success", False),
            "matches": result.get("matches", False),
            "expected_hash": result.get("expected_hash"),
            "result_hash": result.get("result_hash"),
        }
    except Exception as e:
        return {
            "tx_hash": tx_hash,
            "dump_dir": dump_dir,
            "success": False,
            "matches": False,
            "error": str(e),
        }


def run_batch(run_dir: str, emulator_path: str, parallel: int = 1) -> Dict:
    """Run batch replay and return results."""
    failed_dir = os.path.join(run_dir, "failed")
    
    if not os.path.isdir(failed_dir):
        raise RuntimeError(f"No 'failed' directory in {run_dir}")
    
    tx_dirs = []
    for name in os.listdir(failed_dir):
        tx_path = os.path.join(failed_dir, name)
        if os.path.isdir(tx_path):
            tx_dirs.append(tx_path)
    
    if not tx_dirs:
        logger.info("No failed transactions to replay.")
        return {"total": 0, "now_matching": 0, "still_failing": 0, "results": []}
    
    logger.info(f"Replaying {len(tx_dirs)} failed transactions...")
    logger.info(f"Emulator: {emulator_path}")
    
    results = []
    
    if parallel > 1:
        with ProcessPoolExecutor(max_workers=parallel) as executor:
            futures = {
                executor.submit(replay_one, (d, emulator_path)): d 
                for d in tx_dirs
            }
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                status = "MATCH" if result.get("matches") else "FAIL"
                logger.info(f"  {result['tx_hash']}: {status}")
    else:
        for d in tx_dirs:
            result = replay_one((d, emulator_path))
            results.append(result)
            status = "MATCH" if result.get("matches") else "FAIL"
            logger.info(f"  {result['tx_hash']}: {status}")
    
    now_matching = sum(1 for r in results if r.get("matches"))
    still_failing = len(results) - now_matching
    
    summary = {
        "total": len(results),
        "now_matching": now_matching,
        "still_failing": still_failing,
        "results": results,
    }
    
    logger.info(f"")
    logger.info(f"{'='*60}")
    logger.info(f"BATCH REPLAY COMPLETE")
    logger.info(f"  Total:         {len(results)}")
    logger.info(f"  Now matching:  {now_matching} ({now_matching/len(results)*100:.1f}%)")
    logger.info(f"  Still failing: {still_failing}")
    
    if still_failing > 0:
        logger.info(f"")
        logger.info(f"Still failing:")
        for r in results:
            if not r.get("matches"):
                logger.info(f"  - {r['tx_hash']}: {r.get('error', 'hash mismatch')}")
    
    return summary


def main():
    ap = argparse.ArgumentParser(description="Batch replay failed transactions")
    ap.add_argument("--run-dir", required=True, help="Path to debug run directory")
    ap.add_argument("--emulator", required=True, help="Path to emulator .so/.dylib")
    ap.add_argument("--parallel", type=int, default=1, help="Number of parallel workers")
    ap.add_argument("--out", help="Save results to JSON file")
    args = ap.parse_args()

    summary = run_batch(args.run_dir, args.emulator, args.parallel)
    
    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Results saved to {args.out}")

    return 0 if summary["still_failing"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
