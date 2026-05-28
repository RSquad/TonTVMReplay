"""
Save debug dumps for failed transactions.
Creates structured folders with BOC files for debugging.
"""
import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from tonpy import Cell
from tonpy.types import StackEntry

from tonemuso.utils import normalize_prev_blocks_info

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


def _cell_to_bytes(cell: Cell) -> bytes:
    """Convert Cell to bytes."""
    boc = cell.to_boc()
    if isinstance(boc, bytes):
        return boc
    elif isinstance(boc, str):
        # Try hex first, then base64
        try:
            return bytes.fromhex(boc)
        except ValueError:
            return base64.b64decode(boc)
    else:
        return bytes(boc)


def _cell_to_base64(cell: Optional[Cell]) -> Optional[str]:
    """Convert Cell to base64 string."""
    if cell is None:
        return None
    try:
        boc_bytes = _cell_to_bytes(cell)
        return base64.b64encode(boc_bytes).decode('ascii')
    except Exception as e:
        logger.warning(f"Failed to convert cell to base64: {e}")
        return None


def _save_boc(cell: Optional[Cell], path: str) -> bool:
    """Save a Cell as binary BOC file."""
    if cell is None:
        return False
    try:
        boc_bytes = _cell_to_bytes(cell)
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


def _prev_blocks_to_boc_base64(prev_blocks: List[Any]) -> Optional[str]:
    """Serialize prev_blocks to BOC base64 using StackEntry (same as EmulatorExtern)."""
    try:
        boc_str = StackEntry(prev_blocks).serialize().to_boc()
        # to_boc() returns base64 string directly in this case
        if isinstance(boc_str, str):
            # Check if it's already base64 or hex
            try:
                bytes.fromhex(boc_str)
                # It's hex, convert to base64
                return base64.b64encode(bytes.fromhex(boc_str)).decode('ascii')
            except ValueError:
                # Already base64
                return boc_str
        elif isinstance(boc_str, bytes):
            return base64.b64encode(boc_str).decode('ascii')
        return None
    except Exception as e:
        logger.warning(f"Failed to serialize prev_blocks to BOC: {e}")
        return None


class DebugDumper:
    """Manages debug dump directory and saves failed transaction data."""

    def __init__(self, base_dir: str, run_dir: Optional[str] = None, mode: str = "minimal"):
        self.base_dir = base_dir
        self.mode = mode if mode in ("minimal", "full") else "minimal"
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

        if self.mode == "full":
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

        # Generate emulator_test.json for Rust testing
        self._save_emulator_test_json(dump, folder)

        logger.info(f"Saved debug dump: {folder}")
        return folder

    def _save_emulator_test_json(self, dump: TxDebugDump, folder: str):
        """Generate emulator_test.json with all data needed for Rust emulator testing."""
        try:
            # Build block_id array: [workchain, shard, seqno, root_hash_int, file_hash_int]
            bi = dump.block_info
            try:
                root_hash_int = int(bi['root_hash'], 16) if isinstance(bi['root_hash'], str) else bi['root_hash']
                file_hash_int = int(bi['file_hash'], 16) if isinstance(bi['file_hash'], str) else bi['file_hash']
            except (ValueError, TypeError):
                root_hash_int = bi['root_hash']
                file_hash_int = bi['file_hash']
            
            block_id = [
                bi['workchain'],
                bi['shard'],
                bi['seqno'],
                root_hash_int,
                file_hash_int,
            ]

            # Convert rand_seed to hex string
            rand_seed = dump.rand_seed
            if isinstance(rand_seed, int):
                rand_seed_hex = hex(rand_seed)[2:].upper().zfill(64)
                rand_seed_int = rand_seed
            else:
                rand_seed_hex = str(rand_seed).upper().zfill(64)
                try:
                    rand_seed_int = int(rand_seed, 16)
                except (ValueError, TypeError):
                    rand_seed_int = rand_seed

            # Build the test JSON
            emulator_test = {
                "tx_hash": dump.tx_hash,
                "lt": dump.lt,
                "now": dump.now,
                "is_tock": dump.is_tock,
                "block_id": block_id,
                "rand_seed": rand_seed_int,
                "rand_seed_hex": rand_seed_hex,
                "config_params_boc": _cell_to_base64(dump.config_cell),
                "config_params_boc_error": None,
                "libs_boc": _cell_to_base64(dump.libs_cell),
                "libs_boc_error": None,
                "prev_blocks_info_boc": _prev_blocks_to_boc_base64(dump.prev_blocks),
                "prev_blocks_info_boc_error": None,
                "prev_blocks_info": dump.prev_blocks,
                "shard_account_boc": _cell_to_base64(dump.account_before),
                "shard_account_boc_error": None,
                "message_boc": _cell_to_base64(dump.in_msg),
                "message_boc_error": None if dump.in_msg else "no_in_msg",
                "tx_boc": _cell_to_base64(dump.expected_tx),
                "tx_boc_error": None,
            }

            _save_json(emulator_test, os.path.join(folder, "emulator_test.json"))
        except Exception as e:
            logger.warning(f"Failed to generate emulator_test.json: {e}")

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
        expected_tx: Optional[Cell] = None,
        tx_hash_override: Optional[str] = None,
    ) -> str:
        """
        Save debug dump using data available in emulation context.
        This is the main entry point called from common.py/emulation.py
        """
        tx_cell = expected_tx if expected_tx is not None else tx['tx']
        tx_hash = tx_hash_override or tx_cell.get_hash()

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

        prev_blocks = normalize_prev_blocks_info([
            list(block['prev_block_data'][1]),  # no reverse
            block['prev_block_data'][2],
            list(block['prev_block_data'][0]),  # no reverse
        ])

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


def init_dumper(base_dir: Optional[str], run_dir: Optional[str] = None, mode: Optional[str] = None) -> Optional[DebugDumper]:
    """Initialize global dumper. Call once at startup."""
    global _GLOBAL_DUMPER
    if base_dir or run_dir:
        dump_mode = (mode or os.getenv("DEBUG_DUMPS_MODE", "minimal") or "minimal").strip().lower()
        _GLOBAL_DUMPER = DebugDumper(base_dir or "", run_dir=run_dir, mode=dump_mode)
    return _GLOBAL_DUMPER


def get_dumper() -> Optional[DebugDumper]:
    """Get global dumper instance."""
    return _GLOBAL_DUMPER
