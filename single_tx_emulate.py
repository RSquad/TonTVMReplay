#!/usr/bin/env python3
import argparse
import json
import os
import re
from collections import Counter
from multiprocessing import Queue
from time import sleep
from typing import Any, Dict, Optional, Tuple

import requests
from loguru import logger
from tonpy.blockscanner.blockscanner import BlockId, BlockIdExt, BlockScanner, LiteClient

from tonemuso.config import Config
from tonemuso.modes.common import process_blocks, process_result
from tonemuso.utils import b64_to_hex


def _parse_block_tuple(s: str) -> Optional[Tuple[int, int, int]]:
    # Accept "(0,8000000000000000,61926502)" or "0,8000000000000000,61926502"
    m = re.search(r"\(?\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*\)?", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _normalize_hash(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    hex_chars = "0123456789abcdefABCDEF"
    if len(s) == 64 and all(c in hex_chars for c in s):
        return s.upper()
    return b64_to_hex(s, uppercase=True)


def _resolve_via_tonapi(tx_hash: str, base_url: str, api_key: Optional[str]) -> Dict[str, Any]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    tx_url = f"{base_url.rstrip('/')}/v2/blockchain/transactions/{tx_hash}"
    r = requests.get(tx_url, headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"tonapi tx lookup failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    block_field = data.get("block")
    if isinstance(block_field, dict):
        wc = int(block_field.get("workchain_id", 0))
        shard = int(block_field.get("shard", 0))
        seqno = int(block_field.get("seqno", 0))
    else:
        parsed = _parse_block_tuple(str(block_field))
        if not parsed:
            raise RuntimeError(f"tonapi tx lookup: cannot parse block field: {block_field!r}")
        wc, shard, seqno = parsed

    blk_url = f"{base_url.rstrip('/')}/v2/blockchain/blocks/({wc},{shard},{seqno})"
    r = requests.get(blk_url, headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"tonapi block lookup failed: {r.status_code} {r.text[:200]}")
    blk = r.json()
    root_hash = _normalize_hash(blk.get("root_hash", ""))
    file_hash = _normalize_hash(blk.get("file_hash", ""))
    return {
        "hash": tx_hash.upper(),
        "workchain": wc,
        "shard": shard,
        "seqno": seqno,
        "root_hash": root_hash,
        "file_hash": file_hash,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", required=True, help="tx hash (hex)")
    ap.add_argument("--workchain", type=int)
    ap.add_argument("--shard", type=int)
    ap.add_argument("--seqno", type=int)
    ap.add_argument("--root-hash")
    ap.add_argument("--file-hash")
    ap.add_argument("--tonapi", action="store_true", help="resolve block data via tonapi")
    ap.add_argument("--tonapi-base", default=os.getenv("TONAPI_BASE", "https://tonapi.io"))
    ap.add_argument("--tonapi-key", default=os.getenv("TONAPI_KEY"))
    ap.add_argument("--out", default="failed_txs_single.json")
    ap.add_argument("--nproc", type=int)
    ap.add_argument("--chunk-size", type=int)
    ap.add_argument("--tx-chunk-size", type=int)
    ap.add_argument("--loglevel", type=int)
    args = ap.parse_args()

    tx_hash = args.tx.upper()

    # Resolve block info if missing
    if any(v is None for v in (args.workchain, args.shard, args.seqno, args.root_hash, args.file_hash)):
        if not args.tonapi:
            raise SystemExit(
                "Missing block fields. Provide --workchain --shard --seqno --root-hash --file-hash "
                "or pass --tonapi to resolve via tonapi."
            )
        info = _resolve_via_tonapi(tx_hash, args.tonapi_base, args.tonapi_key)
    else:
        info = {
            "hash": tx_hash,
            "workchain": args.workchain,
            "shard": args.shard,
            "seqno": args.seqno,
            "root_hash": _normalize_hash(args.root_hash or ""),
            "file_hash": _normalize_hash(args.file_hash or ""),
        }

    cfg = Config.from_env()
    if args.nproc is not None:
        cfg.nproc = args.nproc
    if args.chunk_size is not None:
        cfg.chunk_size = args.chunk_size
    if args.tx_chunk_size is not None:
        cfg.tx_chunk_size = args.tx_chunk_size
    if args.loglevel is not None:
        cfg.loglevel = args.loglevel

    txs_whitelist = {tx_hash}
    blocks_to_load = [
        BlockIdExt(
            BlockId(
                workchain=info["workchain"],
                shard=info["shard"],
                seqno=info["seqno"],
            ),
            root_hash=info["root_hash"],
            file_hash=info["file_hash"],
        )
    ]

    lcparams = cfg.lcparams()
    # Fail fast if LS params missing
    if not cfg.liteserver_ip or not cfg.liteserver_port or not cfg.liteserver_pubkey:
        raise SystemExit("LITESERVER_* env not set (SERVER, PORT, PUBKEY).")

    outq = Queue()
    raw_proc = process_blocks(
        config_override=cfg.c7_rewrite,
        trace_whitelist=None,
        loglevel=cfg.loglevel,
        color_schema=cfg.color_schema,
        emulator_path=cfg.emulator_path,
        emulator_unchanged_path=cfg.emulator_unchanged_path,
        txs_whitelist=txs_whitelist,
    )

    scanner = BlockScanner(
        lcparams=lcparams,
        start_from=None,
        load_to=None,
        nproc=int(cfg.nproc),
        loglevel=cfg.loglevel,
        chunk_size=int(cfg.chunk_size),
        tx_chunk_size=int(cfg.tx_chunk_size),
        raw_process=raw_proc,
        out_queue=outq,
        only_mc_blocks=bool(cfg.only_mc_blocks),
        parse_txs_over_ls=bool(cfg.parse_over_ls),
        blocks_to_load=blocks_to_load,
    )

    logger.warning(f"Single tx emulate: {tx_hash}")
    logger.warning(
        f"Block: wc={info['workchain']} shard={info['shard']} seqno={info['seqno']} "
        f"root={info['root_hash']} file={info['file_hash']}"
    )

    scanner.start()

    success = 0
    warnings = 0
    unsuccess = []
    while not scanner.done:
        tmp_s, tmp_u, tmp_w = process_result(outq, loglevel=cfg.loglevel)
        success += tmp_s
        warnings += tmp_w
        unsuccess.extend(tmp_u)
        sleep(1)

    tmp_s, tmp_u, tmp_w = process_result(outq, loglevel=cfg.loglevel)
    success += tmp_s
    warnings += tmp_w
    unsuccess.extend(tmp_u)

    logger.warning(f"Final emulator status: {success} success, {len(unsuccess)} unsuccess, {warnings} warnings")
    if unsuccess:
        cnt = Counter()
        for i in unsuccess:
            if isinstance(i, dict) and i.get("address"):
                cnt[i["address"]] += 1
        logger.error(f"Unique addreses errors: {len(cnt)}, most common: ")
        logger.error(cnt.most_common(5))

    with open(args.out, "w") as f:
        json.dump(unsuccess, f, indent=2)
    logger.warning(f"Wrote failed txs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
