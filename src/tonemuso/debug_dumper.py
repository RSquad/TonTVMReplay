"""
Save debug dumps for failed transactions.
Creates structured folders with BOC files for debugging.
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from tonpy import Cell


@dataclass
class TxDebugDump:
    """All data needed to debug a failed transaction."""
    tx_hash: str
    address: str
    block_info: Dict[str, Any]
    prev_blocks: List[Any]
    config_cell: Cell
    libs_cell: Cell
    rand_seed: str
    account_before: Cell
    in_msg: Optional[Cell]
    expected_tx: Cell
    em1_tx: Optional[Cell]
    em2_tx: Optional[Cell]
    em1_account_after: Optional[Cell]
    em2_account_after: Optional[Cell]
    error_info: Dict[str, Any]
    lt: int
    now: int
    is_tock: bool


def _save_boc(cell: Optional[Cell], path: str) -> bool:
    """Save a Cell as binary BOC file."""
    if cell is None:
        return False
    try:
        boc = cell.to_boc()
        if isinstance(boc, bytes):
            boc_bytes = boc
        elif isinstance(boc, str):
            # Try hex first, then base64
            try:
                boc_bytes = bytes.fromhex(boc)
            except ValueError:
                import base64
                boc_bytes = base64.b64decode(boc)
        else:
            boc_bytes = bytes(boc)
        with open(path, "wb") as f:
            f.write(boc_bytes)
        return True
    except Exception as e:
        logger.warning(f"Failed to save BOC to {path}: {e}")
        return False


def _save_json(data: Any, path: str):
    """Save data as JSON file."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


class DebugDumper:
    """Manages debug dump directory and saves failed transaction data."""

    def __init__(self, base_dir: str, run_dir: Optional[str] = None):
        self.base_dir = base_dir
        if run_dir:
            self.run_dir = run_dir
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.run_dir = os.path.join(base_dir, f"run_{timestamp}")
        self.failed_dir = os.path.join(self.run_dir, "failed")
        os.makedirs(self.failed_dir, exist_ok=True)
        logger.info(f"Debug dumps will be saved to: {self.run_dir}")

    def save_dump(self, dump: TxDebugDump) -> str:
        """Save all debug data for a failed tx. Returns folder path."""
        tx_prefix = dump.tx_hash[:8]
        folder = os.path.join(self.failed_dir, tx_prefix)
        os.makedirs(folder, exist_ok=True)

        _save_boc(dump.account_before, os.path.join(folder, "account_before.boc"))
        _save_boc(dump.in_msg, os.path.join(folder, "in_msg.boc"))
        _save_boc(dump.expected_tx, os.path.join(folder, "expected_tx.boc"))
        _save_boc(dump.em1_tx, os.path.join(folder, "em1_tx.boc"))
        _save_boc(dump.em2_tx, os.path.join(folder, "em2_tx.boc"))
        _save_boc(dump.em1_account_after, os.path.join(folder, "em1_account_after.boc"))
        _save_boc(dump.em2_account_after, os.path.join(folder, "em2_account_after.boc"))
        _save_boc(dump.config_cell, os.path.join(folder, "config.boc"))
        _save_boc(dump.libs_cell, os.path.join(folder, "libs.boc"))

        _save_json(dump.block_info, os.path.join(folder, "block_info.json"))
        _save_json(dump.prev_blocks, os.path.join(folder, "prev_blocks.json"))

        error_full = {
            **dump.error_info,
            "tx_hash": dump.tx_hash,
            "address": dump.address,
            "lt": dump.lt,
            "now": dump.now,
            "is_tock": dump.is_tock,
            "rand_seed": dump.rand_seed,
        }
        _save_json(error_full, os.path.join(folder, "error_info.json"))

        logger.info(f"Saved debug dump: {folder}")
        return folder

    def save_from_emulation_context(
        self,
        tx: Dict[str, Any],
        block: Dict[str, Any],
        account_state_before: Cell,
        em1_tx: Optional[Cell],
        em2_tx: Optional[Cell],
        em1_account: Optional[Cell],
        em2_account: Optional[Cell],
        error_info: Dict[str, Any],
    ) -> str:
        """
        Save debug dump using data available in emulation context.
        This is the main entry point called from common.py/emulation.py
        """
        tx_cell = tx['tx']
        tx_hash = tx_cell.get_hash()

        cs = tx_cell.begin_parse()
        tmp = cs.load_ref(as_cs=True)
        in_msg = tmp.load_ref() if tmp.load_bool() else None

        blk_id = block['block_id']
        block_info = {
            "workchain": blk_id.id.workchain,
            "shard": blk_id.id.shard,
            "seqno": blk_id.id.seqno,
            "root_hash": blk_id.root_hash,
            "file_hash": blk_id.file_hash,
        }

        prev_blocks = [
            list(reversed(block['prev_block_data'][1])),
            block['prev_block_data'][2],
            list(reversed(block['prev_block_data'][0])),
        ]

        dump = TxDebugDump(
            tx_hash=tx_hash,
            address=error_info.get('address', 'UNKNOWN'),
            block_info=block_info,
            prev_blocks=prev_blocks,
            config_cell=Cell(block['key_block']['config']),
            libs_cell=Cell(block['libs']),
            rand_seed=block['rand_seed'],
            account_before=account_state_before,
            in_msg=in_msg,
            expected_tx=tx_cell,
            em1_tx=em1_tx,
            em2_tx=em2_tx,
            em1_account_after=em1_account,
            em2_account_after=em2_account,
            error_info=error_info,
            lt=tx['lt'],
            now=tx['now'],
            is_tock=tx['is_tock'],
        )

        return self.save_dump(dump)


_GLOBAL_DUMPER: Optional[DebugDumper] = None


def init_dumper(base_dir: Optional[str], run_dir: Optional[str] = None) -> Optional[DebugDumper]:
    """Initialize global dumper. Call once at startup."""
    global _GLOBAL_DUMPER
    if base_dir or run_dir:
        _GLOBAL_DUMPER = DebugDumper(base_dir or "", run_dir=run_dir)
    return _GLOBAL_DUMPER


def get_dumper() -> Optional[DebugDumper]:
    """Get global dumper instance."""
    return _GLOBAL_DUMPER
