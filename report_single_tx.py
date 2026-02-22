#!/usr/bin/env python3
import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _iter_json_files(root: str) -> List[str]:
    out: List[str] = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".json"):
                out.append(os.path.join(dirpath, name))
    return out


def _find_json_with_substring(roots: List[str], needle: str) -> List[str]:
    matches: List[str] = []
    for root in roots:
        for path in _iter_json_files(root):
            try:
                with open(path, "r") as f:
                    txt = f.read()
                if needle in txt:
                    matches.append(path)
            except Exception:
                continue
    return matches


def _pick_precall(paths: List[str], tx_hash: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    for path in paths:
        data = _read_json(path)
        if isinstance(data, dict) and data.get("tx_hash") == tx_hash:
            return path, data
    return None


def _pick_post_meta(paths: List[str], tx_hash: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    for path in paths:
        data = _read_json(path)
        if isinstance(data, dict) and data.get("expected_tx_hash") == tx_hash:
            return path, data
    return None


def _find_failed_entries(paths: List[str], tx_hash: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for path in paths:
        data = _read_json(path)
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("expected") == tx_hash:
                    e = dict(e)
                    e["_source"] = path
                    entries.append(e)
    return entries


def _find_prev_blocks_files(seqno: Optional[int]) -> List[str]:
    if seqno is None:
        return []
    roots = ["prev_blocks_dump", "dump/prev_blocks"]
    hits: List[str] = []
    token = f"_seq{seqno}_"
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if token in name:
                hits.append(os.path.join(root, name))
    return hits


def _load_txs_to_process(path: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    txs = data.get("transactions")
    if not isinstance(txs, list):
        return None
    for t in txs:
        if isinstance(t, dict) and t.get("hash") == tx_hash:
            return t
    return None


def _summarize_failed(e: Dict[str, Any]) -> str:
    fail_reason = e.get("fail_reason")
    got = e.get("got")
    address = e.get("address")
    color = e.get("color_schema_log") or {}
    affected = color.get("affected_paths")
    affected_n = len(affected) if isinstance(affected, list) else None
    parts = [f"fail_reason={fail_reason}"]
    if got:
        parts.append(f"got={got}")
    if address:
        parts.append(f"address={address}")
    if affected_n is not None:
        parts.append(f"affected_paths={affected_n}")
    return ", ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", required=True, help="tx hash (hex)")
    ap.add_argument("--addr", default=None, help="address hex (without 0:)")
    ap.add_argument("--out", default=None, help="output markdown path")
    ap.add_argument("--txs-to-process", default="txs_to_process.json")
    args = ap.parse_args()

    tx_hash = args.tx.upper()
    out_path = args.out or f"report_{tx_hash}.md"

    # Locate dumps
    precall_candidates = _find_json_with_substring(
        ["emulator_inputs", "runs", "dump/precall"], tx_hash
    )
    post_candidates = _find_json_with_substring(
        ["emulator_post_bocs", "runs", "dump/post", "packs"], tx_hash
    )
    precall = _pick_precall(precall_candidates, tx_hash)
    post = _pick_post_meta(post_candidates, tx_hash)

    failed_entries = _find_failed_entries(
        ["failed_txs.json", "failed_txs/failed_txs_old.json", "failed_txs_old.json"],
        tx_hash,
    )

    txs_info = None
    if os.path.isfile(args.txs_to_process):
        txs_info = _load_txs_to_process(args.txs_to_process, tx_hash)

    prev_blocks_files: List[str] = []
    if txs_info and isinstance(txs_info.get("seqno"), int):
        prev_blocks_files = _find_prev_blocks_files(txs_info["seqno"])

    # Build report
    lines: List[str] = []
    lines.append(f"# Tx report: {tx_hash}")
    lines.append("")
    if args.addr:
        lines.append(f"- Address: `{args.addr}`")
    lines.append("")

    if txs_info:
        lines.append("**txs_to_process.json**")
        lines.append(f"- workchain: `{txs_info.get('workchain')}`")
        lines.append(f"- shard: `{txs_info.get('shard')}`")
        lines.append(f"- seqno: `{txs_info.get('seqno')}`")
        lines.append(f"- root_hash: `{txs_info.get('root_hash')}`")
        lines.append(f"- file_hash: `{txs_info.get('file_hash')}`")
        lines.append("")

    if precall:
        path, data = precall
        lines.append("**Pre-emulation dump**")
        lines.append(f"- file: `{path}`")
        lines.append(f"- account_addr: `{data.get('account_addr')}`")
        lines.append(f"- lt: `{data.get('lt')}`")
        lines.append(f"- now: `{data.get('now')}`")
        lines.append(f"- is_tock: `{data.get('is_tock')}`")
        lines.append(f"- in_msg_hash: `{data.get('in_msg_hash')}`")
        lines.append(f"- tx_boc_b64: {'present' if data.get('tx_boc_b64') else 'missing'}")
        lines.append(f"- in_msg_boc_b64: {'present' if data.get('in_msg_boc_b64') else 'missing'}")
        lines.append(f"- state1_boc_b64: {'present' if data.get('state1_boc_b64') else 'missing'}")
        lines.append(f"- state2_boc_b64: {'present' if data.get('state2_boc_b64') else 'missing'}")
        lines.append("")

    if post:
        path, data = post
        lines.append("**Post-emulation dump**")
        lines.append(f"- file: `{path}`")
        lines.append(f"- em1_tx_b64: `{data.get('em1_tx_b64')}`")
        lines.append(f"- em2_tx_b64: `{data.get('em2_tx_b64')}`")
        lines.append(f"- em1_account_b64: `{data.get('em1_account_b64')}`")
        lines.append(f"- em2_account_b64: `{data.get('em2_account_b64')}`")
        lines.append("")

    if failed_entries:
        lines.append("**failed_txs entries**")
        for e in failed_entries:
            lines.append(f"- {_summarize_failed(e)} (source `{e.get('_source')}`)")
        lines.append("")

    if prev_blocks_files:
        lines.append("**Prev blocks dumps (by shard seqno)**")
        for p in prev_blocks_files:
            lines.append(f"- `{p}`")
        lines.append("")

    if not any([precall, post, failed_entries, prev_blocks_files]):
        lines.append("No local dumps or failed_txs entries found for this tx.")
        lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Wrote report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
