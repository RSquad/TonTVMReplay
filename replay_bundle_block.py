#!/usr/bin/env python3
import argparse
import json
import os
import re
from base64 import b64encode
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger
from tonpy import Cell, VmDict, begin_cell
from tonpy.autogen.block import (
    Block,
    BlockExtra,
    BlockInfo,
    ConfigParams,
    DepthBalanceInfo,
    KeyExtBlkRef,
    KeyMaxLt,
    ShardAccount,
    ShardAccounts,
    ShardState,
    Transaction,
)
from tonpy.blockscanner.blockscanner import BlockId, BlockIdExt, convert_account_blocks_to_txs
from tonpy.types.vmdict import TypedAugmentedData
from tonpy.utils.shard_account import shard_is_ancestor

from tonemuso.config import Config
from tonemuso.debug_dumper import DebugDumper, init_dumper
from tonemuso.diff import get_diff, get_shard_account_diff, make_json_dumpable
from tonemuso.emulation import TxStepEmulator, init_emulators
from tonemuso.modes.common import process_blocks


BLOCK_ID_RE = re.compile(
    r"^\((?P<wc>-?\d+):(?P<shard>[0-9a-fA-F]+),\s*"
    r"(?P<seqno>\d+),\s*rh\s+(?P<root>[0-9a-fA-F]+),\s*fh\s+(?P<file>[0-9a-fA-F]+)\)$"
)


def _load_boc_file(path: Path) -> Cell:
    return Cell(b64encode(path.read_bytes()).decode())


def _load_env_or_bundle_libs_cell(bundle_libs_boc: Optional[str]) -> str:
    libs_path = os.getenv("EMULATOR_LIBS_BOC_PATH", "").strip()
    if not libs_path:
        if bundle_libs_boc is None:
            raise RuntimeError("Bundle does not contain libs and EMULATOR_LIBS_BOC_PATH is not set")
        return bundle_libs_boc

    raw = Path(libs_path).read_bytes().strip()
    try:
        text = raw.decode("ascii").strip()
        try:
            Cell(text)
            logger.info("Using bundle block['libs'] from EMULATOR_LIBS_BOC_PATH: {}", libs_path)
            return text
        except Exception:
            cell = Cell(b64encode(bytes.fromhex(text)).decode("ascii"))
            logger.info("Using bundle block['libs'] from EMULATOR_LIBS_BOC_PATH: {}", libs_path)
            return cell.to_boc()
    except UnicodeDecodeError:
        cell = Cell(b64encode(raw).decode("ascii"))
        logger.info("Using bundle block['libs'] from EMULATOR_LIBS_BOC_PATH: {}", libs_path)
        return cell.to_boc()


def _parse_block_id(raw: str) -> BlockIdExt:
    m = BLOCK_ID_RE.match(raw.strip())
    if not m:
        raise ValueError(f"Bad block id string: {raw}")
    wc = int(m.group("wc"))
    shard = int(m.group("shard"), 16)
    seqno = int(m.group("seqno"))
    root = m.group("root").upper()
    file_hash = m.group("file").upper()
    return BlockIdExt(BlockId(wc, shard, seqno), root_hash=root, file_hash=file_hash)


def _blk_to_data(blk: BlockIdExt) -> List[int]:
    return [
        blk.id.workchain,
        blk.id.shard,
        blk.id.seqno,
        int(str(blk.root_hash), 16),
        int(str(blk.file_hash), 16),
    ]


def _ext_ref_to_data(ref: Any, workchain: int = -1, shard: int = 0x8000000000000000) -> List[int]:
    return [
        workchain,
        shard,
        int(ref.seq_no),
        int(ref.root_hash, 2),
        int(ref.file_hash, 2),
    ]


def _ext_ref_to_block_id_ext(ref: Any, workchain: int = -1, shard: int = 0x8000000000000000) -> BlockIdExt:
    return BlockIdExt(
        BlockId(workchain, shard, int(ref.seq_no)),
        root_hash=f"{int(ref.root_hash, 2):064X}",
        file_hash=f"{int(ref.file_hash, 2):064X}",
    )


def _tx_prev_marker(tx_cell: Cell) -> Tuple[int, str]:
    tx = Transaction().cell_unpack(tx_cell, True)
    prev_hash = f"{int(tx.prev_trans_hash, 2):064X}"
    return int(tx.prev_trans_lt), prev_hash


def _account_addr_bits_to_hex(raw: Any) -> str:
    text = str(raw)
    if set(text) <= {"0", "1"}:
        return f"{int(text, 2):064X}"
    return text.upper()


def _hash_to_filename(raw: Any) -> str:
    text = str(raw).strip()
    if not text:
        raise ValueError("empty hash value")
    return f"{int(text, 16):064x}"


def _shard_account_prev_marker(shard_account_cell: Cell) -> Tuple[int, str]:
    rec = ShardAccount().cell_unpack(shard_account_cell, False)
    last_hash = f"{int(rec.last_trans_hash, 2):064X}"
    return int(rec.last_trans_lt), last_hash


def _empty_shard_account_cell() -> Cell:
    return (
        begin_cell()
        .store_ref(begin_cell().store_uint(0, 1).end_cell())
        .store_uint(0, 256)
        .store_uint(0, 64)
        .end_cell()
    )


def _build_prev_blocks(master_seqno: int, old_mc_vm: VmDict, current_mc: BlockIdExt) -> List[List[int]]:
    out: List[List[int]] = []
    for seqno in range(master_seqno - 15, master_seqno + 1):
        if seqno == master_seqno:
            out.append(_blk_to_data(current_mc))
            continue
        rec = KeyExtBlkRef().fetch(old_mc_vm.lookup(seqno).data.copy(), True)
        out.append(_ext_ref_to_data(rec.blk_ref))
    return list(reversed(out))


def _build_prev_blocks_100(master_seqno: int, old_mc_vm: VmDict) -> List[List[int]]:
    out: List[List[int]] = []
    # BlockScanner starts the 100-step chain from the previous completed
    # hundred bucket, not from the current master seqno when it is exactly
    # divisible by 100. Some bundles contain 65854400/65854300... but not
    # 65854500 itself, so matching that logic avoids false CellCreateError.
    seqno = int((master_seqno - 1) - (master_seqno - 1) % 100)
    while len(out) < 16:
        try:
            rec = KeyExtBlkRef().fetch(old_mc_vm.lookup(seqno).data.copy(), True)
        except Exception as e:
            if out:
                logger.warning(
                    "Bundle prev_blocks_100 truncated at seqno={} after {} entries: {}",
                    seqno,
                    len(out),
                    e,
                )
                break
            raise
        out.append(_ext_ref_to_data(rec.blk_ref))
        if seqno == 100:
            break
        seqno -= 100
    return out


def _resolve_state_file_for_account(
    account: int,
    prev_block_left: BlockIdExt,
    prev_block_right: Optional[BlockIdExt],
    states_dir: Path,
) -> Path:
    if prev_block_right is not None and not shard_is_ancestor(prev_block_left.id.shard, account):
        return states_dir / _hash_to_filename(prev_block_right.root_hash)
    return states_dir / _hash_to_filename(prev_block_left.root_hash)


def _lookup_account_state_from_state_file(state_file: Path, account: int) -> Optional[Cell]:
    state_outer = _load_boc_file(state_file)
    state_inner = state_outer.begin_parse(True).load_ref()
    shard_state = ShardState().cell_unpack(state_inner, False).x
    shard_accounts = ShardAccounts().cell_unpack(shard_state.accounts, False).x
    vm = VmDict(
        256,
        False,
        cell_root=shard_accounts.root,
        aug=TypedAugmentedData(ShardAccount(), DepthBalanceInfo()),
    )
    try:
        rec = ShardAccount().fetch(vm.lookup(account).data.copy(), False)
    except Exception:
        return None
    return rec.cell_pack()


def build_bundle_block(bundle_dir: Path) -> Tuple[Dict[str, Any], List[List[Any]]]:
    index = json.loads((bundle_dir / "index.json").read_text())

    candidate_cell = _load_boc_file(bundle_dir / "candidate" / "data")
    candidate_block = Block().cell_unpack(candidate_cell)
    block_info = BlockInfo().cell_unpack(candidate_block.info, True)
    block_extra = BlockExtra().cell_unpack(candidate_block.extra, False)
    block_id = _parse_block_id(index["id"])

    prev_left = _parse_block_id(index["prev_blocks"][0])
    prev_right = _parse_block_id(index["neighbors"][0]) if block_info.after_merge else None

    current_mc = _parse_block_id(index["last_mc_state"])
    mc_state_outer = _load_boc_file(bundle_dir / "states" / _hash_to_filename(current_mc.root_hash))
    mc_state = ShardState().cell_unpack(mc_state_outer.begin_parse(True).load_ref(), False).x
    mc_extra = mc_state.custom.value
    old_mc_blocks_root = mc_extra.r1.prev_blocks.x.root
    old_mc_vm = VmDict(
        32,
        False,
        cell_root=old_mc_blocks_root,
        aug=TypedAugmentedData(KeyExtBlkRef(), KeyMaxLt()),
    )

    last_key_block = mc_extra.r1.last_key_block.value
    if int(last_key_block.seq_no) != int(block_info.prev_key_block_seqno):
        raise RuntimeError(
            f"Bundle MC state last_key_block seqno {last_key_block.seq_no} "
            f"does not match candidate prev_key_block_seqno {block_info.prev_key_block_seqno}"
        )

    prev_blocks = _build_prev_blocks(block_info.master_ref.master.seq_no, old_mc_vm, current_mc)
    prev_blocks_100 = _build_prev_blocks_100(block_info.master_ref.master.seq_no, old_mc_vm)
    config_params = ConfigParams().fetch(mc_extra.config.copy(), False)
    key_block = {
        "blk_id": _ext_ref_to_block_id_ext(last_key_block),
        "config": config_params.config,
    }

    bundle_libs_boc = mc_state.r1.libraries.root.to_boc() if hasattr(mc_state.r1.libraries, "root") else None
    libs_boc = _load_env_or_bundle_libs_cell(bundle_libs_boc)

    block = {
        "block_id": block_id,
        "rand_seed": int(block_extra.rand_seed, 2),
        "gen_utime": block_info.gen_utime,
        "prev_key_block_seqno": block_info.prev_key_block_seqno,
        "prev_block_left": prev_left,
        "prev_block_right": prev_right,
        "master": block_info.master_ref.master.seq_no,
        "account_blocks": convert_account_blocks_to_txs(block_extra.account_blocks),
        "prev_block_data": [prev_blocks_100, prev_blocks, _ext_ref_to_data(last_key_block)],
        "key_block": key_block,
        "libs": libs_boc,
        "mc_block_id": current_mc,
    }

    states_dir = bundle_dir / "states"
    grouped: List[List[Any]] = []
    for account, txs in block["account_blocks"].items():
        state_file = _resolve_state_file_for_account(account, prev_left, prev_right, states_dir)
        account_state = _lookup_account_state_from_state_file(state_file, account)
        if account_state is None and txs:
            expected_prev = _tx_prev_marker(txs[0]["tx"])
            if expected_prev == (0, "0" * 64):
                account_state = _empty_shard_account_cell()
        if account_state is not None and txs:
            expected_prev = _tx_prev_marker(txs[0]["tx"])
            actual_prev = _shard_account_prev_marker(account_state)
            if expected_prev != actual_prev:
                logger.warning(
                    "Initial account state mismatch for account {}: expected prev {} / {}, got {} / {}",
                    f"0:{account:064X}",
                    expected_prev[0],
                    expected_prev[1],
                    actual_prev[0],
                    actual_prev[1],
                )
        grouped.append([block, account_state, txs])
    return block, grouped


def summarize(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    success = 0
    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    addrs = set()

    for item in results:
        addr = item.get("address")
        if addr:
            addrs.add(addr)
        mode = item.get("mode")
        if mode == "success":
            success += 1
        elif mode == "warning":
            warnings.append(item)
        else:
            errors.append(item)

    return {
        "success": success,
        "warnings_count": len(warnings),
        "errors_count": len(errors),
        "unique_accounts": len(addrs),
        "warning_addresses_top": Counter(x.get("address") for x in warnings if isinstance(x, dict)).most_common(20),
        "error_addresses_top": Counter(x.get("address") for x in errors if isinstance(x, dict)).most_common(20),
        "warnings": warnings,
        "errors": errors,
    }


def compare_emulators_for_bundle(
    grouped: Iterable[List[Any]],
    cfg: Config,
    dumper: Optional[DebugDumper] = None,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    tx_total = 0
    tx_same = 0
    tx_diff = 0
    tx_both_match_candidate = 0
    tx_only_em1_matches = 0
    tx_only_em2_matches = 0
    tx_neither_matches = 0
    cant_emulate = 0

    for block, initial_account_state, txs in grouped:
        em1, em2 = init_emulators(
            block,
            cfg.c7_rewrite,
            emulator_path=cfg.emulator_path,
            emulator_unchanged_path=cfg.emulator_unchanged_path,
        )
        step = TxStepEmulator(
            block=block,
            loglevel=cfg.loglevel,
            color_schema=None,
            em=em1,
            account_state_em1=initial_account_state,
            em2=em2,
            account_state_em2=initial_account_state,
        )

        for tx in sorted(txs, key=lambda x: x["lt"]):
            tx_total += 1
            candidate_hash = tx["tx"].get_hash()
            tx_info = Transaction().cell_unpack(tx["tx"], True)
            address = f"{block['block_id'].id.workchain}:{_account_addr_bits_to_hex(tx_info.account_addr)}"
            account_before = step.state1
            in_msg, lt, now, is_tock = step._prepare_in_msg(tx)

            err1 = None
            err2 = None
            success1 = False
            success2 = False
            try:
                success1 = step._run_primary(in_msg, now, lt, is_tock)
            except Exception as e:
                err1 = str(e)
                step.em.transaction = None
            try:
                success2 = step._run_secondary(in_msg, now, lt, is_tock)
            except Exception as e:
                err2 = str(e)
                step.em2.transaction = None

            em1_tx_hash = step.em.transaction.get_hash() if step.em and step.em.transaction is not None else None
            em2_tx_hash = step.em2.transaction.get_hash() if step.em2 and step.em2.transaction is not None else None
            em1_acc_hash = step.em.account.to_cell().get_hash() if step.em and step.em.account is not None else None
            em2_acc_hash = step.em2.account.to_cell().get_hash() if step.em2 and step.em2.account is not None else None

            same_tx = em1_tx_hash is not None and em1_tx_hash == em2_tx_hash
            same_account = em1_acc_hash is not None and em1_acc_hash == em2_acc_hash
            same = same_tx and same_account
            em1_matches_candidate = em1_tx_hash == candidate_hash
            em2_matches_candidate = em2_tx_hash == candidate_hash

            row = {
                "address": address,
                "lt": lt,
                "now": now,
                "is_tock": is_tock,
                "candidate_tx_hash": candidate_hash,
                "em1_tx_hash": em1_tx_hash,
                "em2_tx_hash": em2_tx_hash,
                "em1_account_hash": em1_acc_hash,
                "em2_account_hash": em2_acc_hash,
                "em1_success": bool(success1),
                "em2_success": bool(success2),
                "em1_error": err1,
                "em2_error": err2,
                "same_tx_hash": same_tx,
                "same_account_hash": same_account,
                "same": same,
                "em1_matches_candidate": em1_matches_candidate,
                "em2_matches_candidate": em2_matches_candidate,
            }

            if em1_tx_hash is None or em2_tx_hash is None or em1_acc_hash is None or em2_acc_hash is None:
                cant_emulate += 1
                row["cant_emulate_compare"] = True
                rows.append(row)
                break

            if same:
                tx_same += 1
            else:
                tx_diff_obj, _ = get_diff(step.em.transaction.to_cell(), step.em2.transaction.to_cell())
                acc_diff_obj = get_shard_account_diff(step.em.account.to_cell(), step.em2.account.to_cell())
                row["diff"] = {
                    "transaction": make_json_dumpable(tx_diff_obj.to_dict()) if tx_diff_obj is not None else None,
                    "account": make_json_dumpable(acc_diff_obj.to_dict()) if acc_diff_obj is not None else None,
                }
                if dumper is not None:
                    row["dump_dir"] = dumper.save_from_emulation_context(
                        tx=tx,
                        block=block,
                        account_state_before=account_before,
                        em1_tx=step.em.transaction.to_cell() if step.em and step.em.transaction is not None else None,
                        em2_tx=step.em2.transaction.to_cell() if step.em2 and step.em2.transaction is not None else None,
                        em1_account=step.em.account.to_cell() if step.em and step.em.account is not None else None,
                        em2_account=step.em2.account.to_cell() if step.em2 and step.em2.account is not None else None,
                        error_info={
                            "mode": "error",
                            "fail_reason": "emulator_compare_diff",
                            "expected": em2_tx_hash,
                            "got": em1_tx_hash,
                            "address": address,
                            "cant_emulate": False,
                            "cant_emulate_em2": False,
                            "account_code_hash": "UNKNOWN",
                            "em1_path": cfg.emulator_path,
                            "em2_path": cfg.emulator_unchanged_path,
                            "em1_matches_candidate": em1_matches_candidate,
                            "em2_matches_candidate": em2_matches_candidate,
                            "same_tx_hash": same_tx,
                            "same_account_hash": same_account,
                            "diff": row["diff"],
                        },
                        expected_tx=step.em2.transaction.to_cell() if step.em2 and step.em2.transaction is not None else tx["tx"],
                        tx_hash_override=em2_tx_hash,
                    )
                tx_diff += 1
                rows.append(row)

            if em1_matches_candidate and em2_matches_candidate:
                tx_both_match_candidate += 1
            elif em1_matches_candidate:
                tx_only_em1_matches += 1
                if same:
                    rows.append(row)
            elif em2_matches_candidate:
                tx_only_em2_matches += 1
                if same:
                    rows.append(row)
            else:
                tx_neither_matches += 1
                if same:
                    rows.append(row)

            step.state1 = step.em.account.to_cell()
            step.state2 = step.em2.account.to_cell()

    unique_rows: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        key = (row["address"], row["lt"], row.get("em1_tx_hash"), row.get("em2_tx_hash"))
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    return {
        "em1_path": cfg.emulator_path,
        "em2_path": cfg.emulator_unchanged_path,
        "tx_total": tx_total,
        "tx_same": tx_same,
        "tx_diff": tx_diff,
        "cant_emulate": cant_emulate,
        "tx_both_match_candidate": tx_both_match_candidate,
        "tx_only_em1_matches_candidate": tx_only_em1_matches,
        "tx_only_em2_matches_candidate": tx_only_em2_matches,
        "tx_neither_matches_candidate": tx_neither_matches,
        "differences": unique_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline replay of a candidate shard block from a local bundle")
    ap.add_argument("--bundle-dir", required=True, help="Path to bundle directory, e.g. bundles/bundle_69599746")
    ap.add_argument("--summary-out", default="/tmp/replay_bundle_block_result.json")
    ap.add_argument("--compare-emulators", action="store_true", help="Compare primary vs secondary emulator results tx-by-tx")
    args = ap.parse_args()

    cfg = Config.from_env()
    cfg.nproc = 1
    cfg.loglevel = max(int(cfg.loglevel), 2)

    dumper = init_dumper(cfg.debug_dumps_dir, mode=cfg.debug_dumps_mode)
    run_dir = dumper.run_dir if dumper else None

    bundle_dir = Path(args.bundle_dir)
    block, grouped = build_bundle_block(bundle_dir)
    logger.warning(
        "Bundle replay: block {} seqno={} accounts={} txs={} dump_dir={}",
        block["block_id"].id.shard,
        block["block_id"].id.seqno,
        len(grouped),
        sum(len(x[2]) for x in grouped),
        run_dir,
    )

    worker = process_blocks(
        config_override=cfg.c7_rewrite,
        trace_whitelist=None,
        loglevel=cfg.loglevel,
        color_schema=cfg.color_schema,
        emulator_path=cfg.emulator_path,
        emulator_unchanged_path=cfg.emulator_unchanged_path,
        txs_whitelist=None,
        debug_dumps_run_dir=run_dir,
    )

    all_results: List[Dict[str, Any]] = []
    for item in grouped:
        all_results.extend(worker(item))

    summary = summarize(all_results)
    if args.compare_emulators:
        summary["emulator_compare"] = compare_emulators_for_bundle(grouped, cfg, dumper=dumper)
    summary.update(
        {
            "bundle_dir": str(bundle_dir),
            "run_dir": run_dir,
            "block_id": _blk_to_data(block["block_id"]),
            "accounts_groups": len(grouped),
            "total_txs": sum(len(x[2]) for x in grouped),
        }
    )

    with open(args.summary_out, "w") as f:
        json.dump(summary, f, indent=2)

    logger.warning(
        json.dumps(
            {
                "bundle_dir": summary["bundle_dir"],
                "run_dir": summary["run_dir"],
                "accounts_groups": summary["accounts_groups"],
                "total_txs": summary["total_txs"],
                "success": summary["success"],
                "warnings_count": summary["warnings_count"],
                "errors_count": summary["errors_count"],
                "unique_accounts": summary["unique_accounts"],
                "compare_tx_diff": summary.get("emulator_compare", {}).get("tx_diff"),
                "compare_cant_emulate": summary.get("emulator_compare", {}).get("cant_emulate"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
