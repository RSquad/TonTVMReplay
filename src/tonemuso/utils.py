# Copyright (c) 2024 Disintar LLP Licensed under the Apache License Version 2.0
import base64
from typing import Optional, Dict, Any, List


def b64_to_hex(s: str, uppercase: bool = True) -> str:
    """
    Convert base64 string to hex string.
    If conversion fails, return the original string uppercased if uppercase=True.
    """
    try:
        h = base64.b64decode(s).hex()
        return h.upper() if uppercase else h
    except Exception:
        return s.upper() if uppercase else s


def hex_to_b64(s: str) -> str:
    """
    Convert hex string to base64 string.
    If conversion fails, return the original string.
    """
    try:
        return base64.b64encode(bytes.fromhex(s)).decode('ascii')
    except Exception:
        return s


def count_modes_tree(node: Dict[str, Any]) -> Dict[str, int]:
    """
    Count modes in a nested emulated_trace tree structure.
    Returns a dict with keys: success, warnings, unsuccess, new, missed
    """
    from collections import Counter
    cnt = Counter()

    def dfs(n):
        if not isinstance(n, dict):
            return
        mode = n.get('mode')
        if mode == 'success':
            cnt['success'] += 1
        elif mode == 'warning':
            cnt['warnings'] += 1
        elif mode == 'error':
            cnt['unsuccess'] += 1
        elif mode == 'new_transaction':
            cnt['new'] += 1
        elif mode == 'missed_transaction':
            cnt['missed'] += 1
        for ch in (n.get('children') or []):
            dfs(ch)

    dfs(node)
    return cnt


def normalize_prev_blocks_info(prev_blocks: Any) -> Any:
    """
    Normalize PrevBlocksInfo structure for emulator:
    [ last_mc_blocks:[BlockId...], prev_key_block:BlockId, last_mc_blocks_100:[BlockId...] ].
    Ensures last_mc_blocks and last_mc_blocks_100 are at most 16 items and removes
    accidental duplicated halves seen in some scanners.
    """
    if not isinstance(prev_blocks, list) or len(prev_blocks) < 3:
        return prev_blocks

    def _is_block_id_list(item: Any) -> bool:
        return isinstance(item, list) and len(item) == 5

    def _dedupe_blocks(blocks: List[Any], name: str) -> List[Any]:
        if not isinstance(blocks, list):
            return blocks
        n = len(blocks)
        if n <= 16:
            return list(blocks)

        # If list is duplicated (common bug), drop the second half.
        if n % 2 == 0 and blocks[: n // 2] == blocks[n // 2:]:
            blocks = blocks[: n // 2]
            n = len(blocks)

        # Dedupe while preserving order, then cap to 16.
        out = []
        seen = set()
        for b in blocks:
            key = tuple(b) if isinstance(b, list) else b
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
            if len(out) >= 16:
                break

        if n > 16:
            try:
                from loguru import logger
                logger.warning(f"normalize_prev_blocks_info: {name} trimmed {n} -> {len(out)}")
            except Exception:
                pass
        return out

    last_mc = prev_blocks[0]
    prev_key = prev_blocks[1]
    last_mc_100 = prev_blocks[2]

    # Only normalize lists of BlockId tuples. Avoid touching single BlockId.
    if isinstance(last_mc, list) and (len(last_mc) == 0 or _is_block_id_list(last_mc[0])):
        last_mc = _dedupe_blocks(last_mc, "last_mc_blocks")
    if isinstance(last_mc_100, list) and (len(last_mc_100) == 0 or _is_block_id_list(last_mc_100[0])):
        last_mc_100 = _dedupe_blocks(last_mc_100, "last_mc_blocks_100")

    return [last_mc, prev_key, last_mc_100]
