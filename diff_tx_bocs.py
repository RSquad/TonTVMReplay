#!/usr/bin/env python3
"""
Compare two transaction BOC files and show differences.

Usage:
    python diff_tx_bocs.py expected.boc actual.boc
    python diff_tx_bocs.py --dump-dir ./debug_dumps/run_.../failed/ABCD1234
"""
import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

from tonpy import Cell, VmDict
from tonpy.autogen.block import Transaction, MessageAny


def _load_boc(path: str) -> Cell:
    with open(path, "rb") as f:
        return Cell(f.read().hex())


def _safe_get(obj: Any, *keys, default=None) -> Any:
    """Safely traverse nested dicts/objects."""
    for k in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(k, default)
        elif hasattr(obj, k):
            obj = getattr(obj, k, default)
        else:
            return default
    return obj


def parse_tx_deep(cell: Cell) -> Dict[str, Any]:
    """Parse transaction into a detailed dict for comparison."""
    tx = Transaction().cell_unpack(cell, True)
    tx_dict = tx.dump()
    
    result = {
        "hash": cell.get_hash(),
        "account_addr": tx_dict.get("account_addr"),
        "lt": tx_dict.get("lt"),
        "prev_trans_hash": tx_dict.get("prev_trans_hash"),
        "prev_trans_lt": tx_dict.get("prev_trans_lt"),
        "now": tx_dict.get("now"),
        "outmsg_cnt": tx_dict.get("outmsg_cnt"),
    }

    r1 = tx_dict.get("r1", {})
    
    total_fees = _safe_get(r1, "total_fees", "grams", "amount", "value")
    result["total_fees"] = total_fees

    desc = r1.get("description", {})
    desc_tag = desc.get("_") if isinstance(desc, dict) else None
    result["description_type"] = desc_tag

    if desc_tag == "trans_ord":
        x = desc.get("x", {})
        result["credit_first"] = x.get("credit_first")
        result["aborted"] = x.get("aborted")
        result["destroyed"] = x.get("destroyed")
        
        compute = x.get("compute_ph", {})
        compute_tag = compute.get("_")
        result["compute_type"] = compute_tag
        
        if compute_tag == "tr_phase_compute_vm":
            vm = compute.get("x", {})
            result["compute"] = {
                "success": vm.get("success"),
                "gas_used": vm.get("gas_used"),
                "gas_limit": vm.get("gas_limit"),
                "exit_code": vm.get("exit_code"),
                "exit_arg": vm.get("exit_arg"),
                "vm_steps": vm.get("vm_steps"),
            }
        
        action = x.get("action", {})
        if action.get("_") == "just":
            act = action.get("value", {})
            result["action"] = {
                "success": act.get("success"),
                "valid": act.get("valid"),
                "no_funds": act.get("no_funds"),
                "result_code": act.get("result_code"),
                "tot_actions": act.get("tot_actions"),
                "msgs_created": act.get("msgs_created"),
                "action_list_hash": act.get("action_list_hash"),
                "tot_msg_size_cells": _safe_get(act, "tot_msg_size", "cells"),
                "tot_msg_size_bits": _safe_get(act, "tot_msg_size", "bits"),
            }

    state_update = tx_dict.get("state_update", {})
    result["state_update"] = {
        "old_hash": state_update.get("old_hash"),
        "new_hash": state_update.get("new_hash"),
    }

    return result


def compare_dicts(d1: Dict, d2: Dict, path: str = "") -> list:
    """Compare two dicts recursively, return list of differences."""
    diffs = []
    
    all_keys = set(d1.keys()) | set(d2.keys())
    for key in sorted(all_keys):
        full_path = f"{path}.{key}" if path else key
        v1 = d1.get(key)
        v2 = d2.get(key)
        
        if v1 == v2:
            continue
        
        if isinstance(v1, dict) and isinstance(v2, dict):
            diffs.extend(compare_dicts(v1, v2, full_path))
        else:
            diffs.append({
                "path": full_path,
                "expected": v1,
                "actual": v2,
            })
    
    return diffs


def format_diff(diffs: list) -> str:
    """Format differences for display."""
    if not diffs:
        return "No differences found."
    
    lines = ["Differences found:", ""]
    for d in diffs:
        lines.append(f"  {d['path']}:")
        lines.append(f"    expected: {d['expected']}")
        lines.append(f"    actual:   {d['actual']}")
        lines.append("")
    return "\n".join(lines)


def diff_transactions(expected: Cell, actual: Cell) -> Tuple[bool, str, list]:
    """Compare two transactions. Returns (match, formatted_diff, raw_diffs)."""
    expected_hash = expected.get_hash()
    actual_hash = actual.get_hash()
    
    if expected_hash == actual_hash:
        return True, "Transactions are identical.", []
    
    try:
        exp_parsed = parse_tx_deep(expected)
        act_parsed = parse_tx_deep(actual)
    except Exception as e:
        return False, f"Failed to parse transactions: {e}", []
    
    diffs = compare_dicts(exp_parsed, act_parsed)
    return False, format_diff(diffs), diffs


def main():
    ap = argparse.ArgumentParser(description="Compare two transaction BOC files")
    ap.add_argument("boc1", nargs="?", help="First BOC file (expected)")
    ap.add_argument("boc2", nargs="?", help="Second BOC file (actual)")
    ap.add_argument("--dump-dir", help="Load from debug dump directory instead")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    args = ap.parse_args()

    if args.dump_dir:
        exp_path = os.path.join(args.dump_dir, "expected_tx.boc")
        
        em1_path = os.path.join(args.dump_dir, "em1_tx.boc")
        em2_path = os.path.join(args.dump_dir, "em2_tx.boc")
        
        if not os.path.exists(exp_path):
            print(f"ERROR: {exp_path} not found", file=sys.stderr)
            return 1
        
        expected = _load_boc(exp_path)
        
        results = {}
        
        if os.path.exists(em1_path):
            actual1 = _load_boc(em1_path)
            match1, diff1, raw1 = diff_transactions(expected, actual1)
            results["em1"] = {"match": match1, "diff": diff1, "raw": raw1}
            
        if os.path.exists(em2_path):
            actual2 = _load_boc(em2_path)
            match2, diff2, raw2 = diff_transactions(expected, actual2)
            results["em2"] = {"match": match2, "diff": diff2, "raw": raw2}

        if os.path.exists(em1_path) and os.path.exists(em2_path):
            match_em, diff_em, raw_em = diff_transactions(actual1, actual2)
            results["em1_vs_em2"] = {"match": match_em, "diff": diff_em, "raw": raw_em}

        if args.json:
            print(json.dumps({k: {"match": v["match"], "diffs": v["raw"]} for k, v in results.items()}, indent=2))
        else:
            for name, r in results.items():
                print(f"=== {name} ===")
                print(r["diff"])
                print()
        
        return 0

    if not args.boc1 or not args.boc2:
        print("ERROR: Provide two BOC files or use --dump-dir", file=sys.stderr)
        return 1

    expected = _load_boc(args.boc1)
    actual = _load_boc(args.boc2)
    
    match, diff, raw = diff_transactions(expected, actual)
    
    if args.json:
        print(json.dumps({"match": match, "diffs": raw}, indent=2))
    else:
        print(diff)
    
    return 0 if match else 1


if __name__ == "__main__":
    raise SystemExit(main())
