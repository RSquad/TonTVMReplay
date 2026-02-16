# Copyright (c) 2024 Disintar LLP Licensed under the Apache License Version 2.0
from typing import List, Optional, Tuple, Dict, Any
import os
import json
import base64
from ctypes import c_int, c_bool

from tonpy import Cell, VmDict, Address
from tonpy.tvm.not_native.emulator_extern import EmulatorExtern
from tonpy.autogen.block import Transaction, MessageAny, ShardAccount
from loguru import logger

from tonemuso.diff import get_diff, get_colored_diff, make_json_dumpable, get_shard_account_diff
from tonemuso.utils import hex_to_b64

# Per-process run folder for pre-emulation dumps
_PRECALL_RUN_DIR: Optional[str] = None
_MASTER_PROOF_LC = None
_MASTER_PROOF_LC_ERR: Optional[str] = None


def _get_master_proof_liteclient():
    global _MASTER_PROOF_LC, _MASTER_PROOF_LC_ERR
    if _MASTER_PROOF_LC is not None or _MASTER_PROOF_LC_ERR is not None:
        return _MASTER_PROOF_LC

    ls_ip = os.getenv("LITESERVER_SERVER")
    ls_port = os.getenv("LITESERVER_PORT")
    ls_pubkey = os.getenv("LITESERVER_PUBKEY")
    ls_timeout = os.getenv("LITESERVER_TIMEOUT", "5")

    if not ls_ip or not ls_port or not ls_pubkey:
        _MASTER_PROOF_LC_ERR = "LITESERVER_* env not set (need SERVER, PORT, PUBKEY)"
        logger.warning(_MASTER_PROOF_LC_ERR)
        return None

    try:
        from tonpy import LiteClient
        server = {
            "ip": int(ls_ip),
            "port": int(ls_port),
            "id": {"@type": "pub.ed25519", "key": ls_pubkey},
        }
        _MASTER_PROOF_LC = LiteClient(
            mode="roundrobin",
            my_rr_servers=[server],
            timeout=float(ls_timeout),
            num_try=5,
            threads=1,
            loglevel=0,
        )
    except Exception as e:
        _MASTER_PROOF_LC_ERR = f"Failed to init LiteClient: {e}"
        logger.warning(_MASTER_PROOF_LC_ERR)
        return None

    return _MASTER_PROOF_LC


def _get_env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        logger.warning(f"{name} is not an int: {raw!r}, using {default}")
        return default


def _create_emulator(emulator_path: str, config: VmDict, vm_log_verbosity: int) -> EmulatorExtern:
    # Try positional and keyword forms; fall back to legacy 2-arg constructor.
    try:
        return EmulatorExtern(emulator_path, config, vm_log_verbosity)
    except TypeError:
        try:
            return EmulatorExtern(emulator_path, config, vm_log_verbosity=vm_log_verbosity)
        except TypeError:
            em = EmulatorExtern(emulator_path, config)
            if vm_log_verbosity > 0:
                logger.warning(
                    "EmulatorExtern() doesn't accept vm_log_verbosity; VM log may be truncated or disabled"
                )
            return em


def init_emulators(block: Dict[str, Any], config_override: Dict[str, Any], emulator_path: str,
                   emulator_unchanged_path: str) -> Tuple[EmulatorExtern, EmulatorExtern]:
    """
    Initialize primary and secondary emulators for a given block using
    the same logic as in main.process_blocks.
    Returns (em, em2)
    """
    # Base config from block key_block (no overrides)
    base_config: VmDict = VmDict(32, False, block['key_block']['config'])

    # Working config (may be overridden via C7_REWRITE)
    config: VmDict = VmDict(32, False, block['key_block']['config'])
    if config_override is not None:
        from tonpy import begin_cell
        for param in config_override:
            config.set(int(param), begin_cell().store_ref(Cell(config_override[param])).end_cell().begin_parse())

    vm_log_verbosity = _get_env_int("EMULATOR_VM_LOG_VERBOSITY", 0)
    em = _create_emulator(emulator_path, config, vm_log_verbosity)
    set_emulator_verbosity(em, env_name="EMULATOR_VERBOSITY", default_level=1)
    em.set_rand_seed(block['rand_seed'])

    prev_block_data = [list(reversed(block['prev_block_data'][1])),  # prev 16
                       block['prev_block_data'][2],  # key block
                       list(reversed(block['prev_block_data'][0]))]  # prev 16 by 100
    em.set_prev_blocks_info(prev_block_data)
    em.set_libs(VmDict(256, False, cell_root=Cell(block['libs'])))

    em2 = _create_emulator(emulator_unchanged_path, base_config, vm_log_verbosity)
    set_emulator_verbosity(em2, env_name="EMULATOR_UNCHANGED_VERBOSITY", default_level=1)
    em2.set_rand_seed(block['rand_seed'])
    em2.set_prev_blocks_info(prev_block_data)
    em2.set_libs(VmDict(256, False, cell_root=Cell(block['libs'])))

    return em, em2


def set_emulator_verbosity(em: EmulatorExtern, env_name: str, default_level: int = 1) -> None:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        level = default_level
    else:
        try:
            level = int(raw)
        except Exception:
            level = default_level
            logger.warning(f"{env_name} is not an int: {raw!r}, using {default_level}")
    try:
        fn = em.libemulator.emulator_set_verbosity_level
        fn.argtypes = [c_int]
        fn.restype = c_bool
        ok = fn(level)
        if not ok:
            logger.warning(f"emulator_set_verbosity_level({level}) returned false for {em.cdll_path}")
    except Exception as e:
        logger.warning(f"Failed to set emulator verbosity via {env_name}: {e}")


def extract_message_info(tx: Cell):
    transaction_data = Transaction().fetch(tx).dump()
    answer = {
        'outmsg_cnt': transaction_data['outmsg_cnt'],
        'out_msgs': []
    }

    if transaction_data['outmsg_cnt'] > 0:
        for _, msg in VmDict(15, False, transaction_data['r1']['out_msgs']['root']):
            msg = msg.load_ref()
            msg_hash = msg.get_hash()
            message_parsed = MessageAny().fetch(msg.copy())
            is_internal = message_parsed.x.info.get_tag() == 0
            message_parsed = message_parsed.dump()

            if is_internal:
                send_body = message_parsed['x'].get('body', {}).get('value', None)

                opcode = None
                bodyhash = None
                if send_body is not None:
                    if isinstance(send_body, Cell):
                        send_body = send_body.begin_parse()
                    bodyhash = send_body.get_hash()
                    if send_body.bits >= 32:
                        opcode = hex(send_body.load_uint(32))

                dest = message_parsed['x']['info']['dest']
                created_lt = message_parsed['x']['info']['created_lt']
                dest = Address(f"{dest['workchain_id']}:{dest['address'].zfill(64)}")
                answer['out_msgs'].append({
                    'msg_hash': msg_hash,
                    'dest': dest,
                    'body': send_body,
                    'opcode': opcode,
                    'bodyhash': bodyhash,
                    'cell': msg,
                    'bounce': message_parsed['x']['info']['bounce'],
                    'bounced': message_parsed['x']['info']['bounced'],
                    'created_lt': created_lt
                })
    answer['out_msgs'] = list(sorted(answer['out_msgs'], key=lambda x: x['created_lt']))
    return answer


class TxStepEmulator:
    """
    Unified transaction emulation stepper. Replaces emulate_tx_step and emulate_tx_step_with_in_msg.
    Now accepts emulators and their account states in __init__.

    Usage:
      step = TxStepEmulator(block=block, loglevel=loglevel, color_schema=color_schema, em=em, account_state_em1=account_state_em1, em2=em2, account_state_em2=account_state_em2)
      out, st1, st2, out_msgs = step.emulate(tx, extract_out_msgs=True)
      # or with override in_msg (em2 is still executed and used for comparison):
      step = TxStepEmulator(block=block, loglevel=loglevel, color_schema=color_schema, em=em, account_state_em1=account_state_em1, em2=em2, account_state_em2=account_state_em2)
      out, st1, st2, out_msgs = step.emulate(tx, override_in_msg=cell, extract_out_msgs=True)

    Returns:
      Tuple[out_entries, new_state_em1, new_state_em2|None, out_msgs|None]
    """

    def __init__(self,
                 block: Dict[str, Any],
                 loglevel: int,
                 color_schema: Optional[Dict[str, Any]],
                 em: EmulatorExtern,
                 account_state_em1: Cell,
                 em2: EmulatorExtern,
                 account_state_em2: Cell,
                 use_boc_for_diff: bool = False) -> None:
        self.block = block
        self.loglevel = loglevel
        self.color_schema = color_schema
        # Emulators and states
        self.em: EmulatorExtern = em
        self.em2: EmulatorExtern = em2
        self.state1: Cell = account_state_em1
        self.state2: Cell = account_state_em2
        # Diff behavior flag: when True, compare BOCs instead of hashes
        self.use_boc_for_diff: bool = bool(use_boc_for_diff)

    # ---- Small helpers to keep emulate() readable ----
    def _prepare_in_msg(self, tx: Dict[str, Any]) -> Tuple[
        Optional[Cell], int, int, bool]:
        lt = tx['lt']
        now = tx['now']
        is_tock = tx['is_tock']

        current_tx_cs = tx['tx'].begin_parse()
        tmp = current_tx_cs.load_ref(as_cs=True)
        if tmp.load_bool():
            in_msg = tmp.load_ref()
        else:
            in_msg = None

        return in_msg, lt, now, is_tock

    def _run_primary(self, in_msg: Optional[Cell], now: int, lt: int, is_tock: bool) -> bool:
        assert self.em is not None and self.state1 is not None
        if in_msg is None:
            return self.em.emulate_tick_tock_transaction(self.state1, is_tock, now, lt)
        return self.em.emulate_transaction(self.state1, in_msg, now, lt)

    def _run_secondary(self, in_msg: Optional[Cell], now: int, lt: int, is_tock: bool) -> bool:
        assert self.em2 is not None and self.state2 is not None
        if in_msg is None:
            return self.em2.emulate_tick_tock_transaction(self.state2, is_tock, now, lt)
        return self.em2.emulate_transaction(self.state2, in_msg, now, lt)

    def _extract_account_code_hash(self):
        try:
            sa = self.em2.account.to_cell()
            sa_o = ShardAccount()
            return sa_o.cell_unpack(sa, True).account.storage.state.x.code.value.get_hash()
        except Exception as e:
            return 'UNKNOWN'

    def _state_to_boc_info(self, state: Any) -> Tuple[Optional[str], Optional[str], str]:
        if state is None:
            return None, "state_none", "NoneType"
        state_type = type(state).__name__
        # Raw bytes support (already a BOC)
        if isinstance(state, (bytes, bytearray, memoryview)):
            try:
                data = bytes(state)
                return base64.b64encode(data).decode(), None, state_type
            except Exception as e:
                return None, f"bytes_encode_error: {e}", state_type
        # tonpy Cell / CellSlice-like
        if hasattr(state, "to_boc"):
            try:
                boc = state.to_boc()
                if isinstance(boc, str):
                    # Some bindings may return base64/hex string already.
                    return boc, "to_boc_returned_str", state_type
                if isinstance(boc, (bytes, bytearray, memoryview)):
                    return base64.b64encode(bytes(boc)).decode(), None, state_type
                return None, f"to_boc_unexpected_type: {type(boc).__name__}", state_type
            except Exception as e:
                return None, f"to_boc_error: {e}", state_type
        return None, "unsupported_state_type", state_type

    def _state_to_boc_bytes(self, state: Any) -> Tuple[Optional[bytes], Optional[str]]:
        if state is None:
            return None, "state_none"
        if isinstance(state, (bytes, bytearray, memoryview)):
            try:
                return bytes(state), None
            except Exception as e:
                return None, f"bytes_error: {e}"
        if hasattr(state, "to_boc"):
            try:
                boc = state.to_boc()
                if isinstance(boc, (bytes, bytearray, memoryview)):
                    return bytes(boc), None
                if isinstance(boc, str):
                    # Try base64 first, then hex
                    try:
                        return base64.b64decode(boc, validate=True), "decoded_base64"
                    except Exception:
                        try:
                            return bytes.fromhex(boc), "decoded_hex"
                        except Exception as e:
                            return None, f"to_boc_str_unparsed: {e}"
                return None, f"to_boc_unexpected_type: {type(boc).__name__}"
            except Exception as e:
                return None, f"to_boc_error: {e}"
        return None, "unsupported_state_type"

    def _normalize_hash(self, h: Any) -> Optional[str]:
        if h is None:
            return None
        if isinstance(h, (bytes, bytearray)):
            return h.hex().upper()
        return str(h)

    def _dump_pre_emulation(self,
                            tx: Dict[str, Any],
                            orig_in_msg: Optional[Cell],
                            override_in_msg: Optional[Cell],
                            lt: int,
                            now: int,
                            is_tock: bool) -> None:
        dump_dir = os.getenv("EMULATOR_PRECALL_DUMP_DIR", "").strip()
        if not dump_dir:
            return

        try:
            os.makedirs(dump_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create EMULATOR_PRECALL_DUMP_DIR={dump_dir!r}: {e}")
            return

        # Ensure per-run subfolder (1, 2, 3, ...)
        global _PRECALL_RUN_DIR
        if _PRECALL_RUN_DIR is None:
            try:
                existing = []
                for name in os.listdir(dump_dir):
                    if name.isdigit():
                        full = os.path.join(dump_dir, name)
                        if os.path.isdir(full):
                            existing.append(int(name))
                next_idx = (max(existing) + 1) if existing else 1
                # Avoid collisions if multiple processes start simultaneously
                for attempt in range(1000):
                    candidate = str(next_idx + attempt)
                    candidate_path = os.path.join(dump_dir, candidate)
                    try:
                        os.makedirs(candidate_path, exist_ok=False)
                        _PRECALL_RUN_DIR = candidate_path
                        break
                    except FileExistsError:
                        continue
                if _PRECALL_RUN_DIR is None:
                    _PRECALL_RUN_DIR = dump_dir
            except Exception as e:
                logger.warning(f"Failed to create run subfolder in {dump_dir!r}: {e}")
                _PRECALL_RUN_DIR = dump_dir

        dump_dir = _PRECALL_RUN_DIR

        account_hex = "UNKNOWN"
        account_addr = None
        wc = None
        try:
            tx_tlb = Transaction().cell_unpack(tx['tx'], True)
            account_address = int(tx_tlb.account_addr, 2)
            account_hex = hex(account_address).upper()[2:].zfill(64)
            wc = getattr(self.block.get('block_id'), 'id', None)
            wc = wc.workchain if wc is not None else None
            if wc is not None:
                account_addr = f"{wc}:{account_hex}"
        except Exception as e:
            logger.debug(f"Pre-emulation dump: failed to parse account addr: {e}")

        tx_cell = None
        try:
            tx_cell = tx['tx']
        except Exception:
            tx_cell = None
        tx_hash = self._normalize_hash(getattr(tx_cell, 'get_hash', lambda: None)())
        base_name = f"{account_hex}_{lt}"
        file_name = f"{base_name}.json"
        file_path = os.path.join(dump_dir, file_name)
        if os.path.exists(file_path):
            suffix = (tx_hash or "NOHASH")[:8]
            file_name = f"{base_name}_{suffix}.json"
            file_path = os.path.join(dump_dir, file_name)

        tx_b64, tx_err, tx_type = self._state_to_boc_info(tx_cell)
        state1_b64, state1_err, state1_type = self._state_to_boc_info(self.state1)
        state2_b64, state2_err, state2_type = self._state_to_boc_info(self.state2)

        in_msg = override_in_msg if override_in_msg is not None else orig_in_msg
        in_msg_source = "override" if override_in_msg is not None else "original"
        if in_msg is None:
            in_msg_source = "none"

        in_msg_hash = self._normalize_hash(getattr(in_msg, 'get_hash', lambda: None)()) if in_msg is not None else None
        in_msg_b64, in_msg_err, in_msg_type = self._state_to_boc_info(in_msg)

        payload = {
            "account_hex": account_hex,
            "account_addr": account_addr,
            "lt": lt,
            "now": now,
            "is_tock": is_tock,
            "tx_hash": tx_hash,
            "tx_boc_b64": tx_b64,
            "tx_boc_error": tx_err,
            "tx_type": tx_type,
            "in_msg_source": in_msg_source,
            "in_msg_hash": in_msg_hash,
            "in_msg_boc_b64": in_msg_b64,
            "in_msg_boc_error": in_msg_err,
            "in_msg_type": in_msg_type,
            "state1_boc_b64": state1_b64,
            "state1_boc_error": state1_err,
            "state1_type": state1_type,
            "state2_boc_b64": state2_b64,
            "state2_boc_error": state2_err,
            "state2_type": state2_type,
        }

        try:
            with open(file_path, "w") as f:
                json.dump(payload, f, ensure_ascii=True)
            if self.loglevel > 3:
                logger.debug(f"Pre-emulation dump saved: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to write pre-emulation dump {file_path!r}: {e}")

    def _dump_post_emulation(self, tx: Dict[str, Any], lt: int, now: int) -> None:
        dump_dir = os.getenv("EMULATOR_POST_DUMP_DIR", "").strip()
        if not dump_dir:
            return

        try:
            os.makedirs(dump_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create EMULATOR_POST_DUMP_DIR={dump_dir!r}: {e}")
            return

        account_hex = "UNKNOWN"
        account_addr = None
        try:
            tx_tlb = Transaction().cell_unpack(tx['tx'], True)
            account_address = int(tx_tlb.account_addr, 2)
            account_hex = hex(account_address).upper()[2:].zfill(64)
            wc = getattr(self.block.get('block_id'), 'id', None)
            wc = wc.workchain if wc is not None else None
            if wc is not None:
                account_addr = f"{wc}:{account_hex}"
        except Exception as e:
            logger.debug(f"Post-emulation dump: failed to parse account addr: {e}")

        tx_hash = self._normalize_hash(getattr(tx['tx'], 'get_hash', lambda: None)())
        base = f"{account_hex}_{lt}"
        if tx_hash:
            base = f"{base}_{tx_hash[:8]}"

        def _write_b64(label: str, cell: Optional[Cell]) -> Optional[str]:
            if cell is None:
                return None
            b64, b64_err, _ = self._state_to_boc_info(cell)
            if b64 is None:
                logger.warning(f"Post-emulation dump: {label} boc error: {b64_err}")
                return None
            path = os.path.join(dump_dir, f"{base}_{label}.b64")
            try:
                with open(path, "w") as f:
                    f.write(b64)
                return path
            except Exception as e:
                logger.warning(f"Failed to write post-emulation {label} {path!r}: {e}")
                return None

        em1_tx = self.em.transaction.to_cell() if self.em is not None and self.em.transaction is not None else None
        em2_tx = self.em2.transaction.to_cell() if self.em2 is not None and self.em2.transaction is not None else None
        em1_acc = self.em.account.to_cell() if self.em is not None and self.em.account is not None else None
        em2_acc = self.em2.account.to_cell() if self.em2 is not None and self.em2.account is not None else None

        em1_path = _write_b64("em1_tx", em1_tx)
        em2_path = _write_b64("em2_tx", em2_tx)
        em1_acc_path = _write_b64("em1_account", em1_acc)
        em2_acc_path = _write_b64("em2_account", em2_acc)

        meta = {
            "account_hex": account_hex,
            "account_addr": account_addr,
            "lt": lt,
            "now": now,
            "expected_tx_hash": tx_hash,
            "em1_tx_hash": self._normalize_hash(getattr(self.em.transaction, 'get_hash', lambda: None)()) if self.em and self.em.transaction is not None else None,
            "em2_tx_hash": self._normalize_hash(getattr(self.em2.transaction, 'get_hash', lambda: None)()) if self.em2 and self.em2.transaction is not None else None,
            "em1_tx_b64": em1_path,
            "em2_tx_b64": em2_path,
            "em1_account_b64": em1_acc_path,
            "em2_account_b64": em2_acc_path,
        }
        meta_path = os.path.join(dump_dir, f"{base}_meta.json")
        try:
            with open(meta_path, "w") as f:
                json.dump(meta, f, ensure_ascii=True)
        except Exception as e:
            logger.warning(f"Failed to write post-emulation meta {meta_path!r}: {e}")

    def _dump_master_proof(self, lt: int, now: int) -> None:
        dump_dir = os.getenv("EMULATOR_MASTER_PROOF_DUMP_DIR", "").strip()
        if not dump_dir:
            return

        block_id = self.block.get("block_id")
        if block_id is None:
            return

        try:
            os.makedirs(dump_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create EMULATOR_MASTER_PROOF_DUMP_DIR={dump_dir!r}: {e}")
            return

        try:
            wc = block_id.id.workchain
            shard = block_id.id.shard
            seqno = block_id.id.seqno
            root_hash = getattr(block_id, "root_hash", None)
            file_hash = getattr(block_id, "file_hash", None)
        except Exception as e:
            logger.warning(f"Failed to extract block_id fields for master proof: {e}")
            return

        def _h_to_hex(h: Any) -> str:
            if h is None:
                return "NONE"
            if isinstance(h, int):
                return f"{h:064X}"
            return str(h).upper()

        shard_hex = hex(shard).upper()[2:]
        root_hex = _h_to_hex(root_hash)
        file_hex = _h_to_hex(file_hash)

        base = f"wc{wc}_sh{shard_hex}_seq{seqno}_rh{root_hex}_fh{file_hex}"
        proof_path = os.path.join(dump_dir, f"{base}_master_proof.boc")
        virt_path = os.path.join(dump_dir, f"{base}_virt_blk_root.boc")
        meta_path = os.path.join(dump_dir, f"{base}_meta.json")

        # Skip if already dumped (avoid per-tx network calls)
        if os.path.exists(proof_path) and os.path.exists(virt_path) and os.path.exists(meta_path):
            return

        lc = _get_master_proof_liteclient()
        if lc is None:
            return

        try:
            hdr = lc.get_block_header(block_id)
        except Exception as e:
            logger.warning(f"Failed to fetch master proof for block {base}: {e}")
            return

        proof_bytes, proof_err = self._state_to_boc_bytes(hdr.proof)
        virt_bytes, virt_err = self._state_to_boc_bytes(hdr.virt_blk_root)

        if proof_bytes is None and virt_bytes is None:
            logger.warning(f"Master proof empty for block {base}: proof_err={proof_err} virt_err={virt_err}")
            return

        try:
            if proof_bytes is not None and not os.path.exists(proof_path):
                with open(proof_path, "wb") as f:
                    f.write(proof_bytes)
            if virt_bytes is not None and not os.path.exists(virt_path):
                with open(virt_path, "wb") as f:
                    f.write(virt_bytes)
            meta = {
                "block_id": {
                    "workchain": wc,
                    "shard": shard_hex,
                    "seqno": seqno,
                    "root_hash": root_hex,
                    "file_hash": file_hex,
                },
                "lt": lt,
                "now": now,
                "proof_file": proof_path if proof_bytes is not None else None,
                "virt_blk_root_file": virt_path if virt_bytes is not None else None,
                "proof_error": proof_err,
                "virt_blk_root_error": virt_err,
            }
            if not os.path.exists(meta_path):
                with open(meta_path, "w") as f:
                    json.dump(meta, f, ensure_ascii=True)
            if self.loglevel > 2:
                logger.debug(f"Master proof dump saved: {meta_path}")
        except Exception as e:
            logger.warning(f"Failed to write master proof for block {base}: {e}")

    def _dump_account_read_failure(self,
                                   tx: Dict[str, Any],
                                   orig_in_msg: Optional[Cell],
                                   override_in_msg: Optional[Cell],
                                   lt: int,
                                   now: int,
                                   is_tock: bool,
                                   missing_em1: bool,
                                   missing_em2: bool) -> None:
        dump_dir = os.getenv("EMULATOR_ACCOUNT_FAIL_DUMP_DIR", "").strip()
        if not dump_dir:
            return

        try:
            os.makedirs(dump_dir, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to create EMULATOR_ACCOUNT_FAIL_DUMP_DIR={dump_dir!r}: {e}")
            return

        account_hex = "UNKNOWN"
        account_addr = None
        try:
            tx_tlb = Transaction().cell_unpack(tx['tx'], True)
            account_address = int(tx_tlb.account_addr, 2)
            account_hex = hex(account_address).upper()[2:].zfill(64)
            wc = getattr(self.block.get('block_id'), 'id', None)
            wc = wc.workchain if wc is not None else None
            if wc is not None:
                account_addr = f"{wc}:{account_hex}"
        except Exception as e:
            logger.debug(f"Account-fail dump: failed to parse account addr: {e}")

        tx_cell = None
        try:
            tx_cell = tx['tx']
        except Exception:
            tx_cell = None
        tx_hash = self._normalize_hash(getattr(tx_cell, 'get_hash', lambda: None)())
        base_name = f"{account_hex}_{lt}"
        if tx_hash:
            base_name = f"{base_name}_{tx_hash[:8]}"

        def _write_boc(label: str, state: Any) -> Dict[str, Any]:
            raw, raw_err = self._state_to_boc_bytes(state)
            if raw is not None:
                path = os.path.join(dump_dir, f"{base_name}_{label}.boc")
                try:
                    with open(path, "wb") as f:
                        f.write(raw)
                    return {"label": label, "file": path, "format": "boc", "info": raw_err}
                except Exception as e:
                    return {"label": label, "error": f"write_boc_error: {e}"}

            b64, b64_err, stype = self._state_to_boc_info(state)
            if b64 is not None:
                path = os.path.join(dump_dir, f"{base_name}_{label}.b64")
                try:
                    with open(path, "w") as f:
                        f.write(b64)
                    return {
                        "label": label,
                        "file": path,
                        "format": "b64",
                        "info": b64_err,
                        "state_type": stype
                    }
                except Exception as e:
                    return {"label": label, "error": f"write_b64_error: {e}"}

            return {"label": label, "error": raw_err or b64_err or "no_data"}

        in_msg = override_in_msg if override_in_msg is not None else orig_in_msg
        in_msg_source = "override" if override_in_msg is not None else ("original" if orig_in_msg is not None else "none")

        entries = [
            _write_boc("state1_before", self.state1),
            _write_boc("state2_before", self.state2),
            _write_boc("tx", tx_cell),
            _write_boc(f"in_msg_{in_msg_source}", in_msg),
        ]

        # If any emulator account is present, dump it too (post-emulate)
        if self.em is not None and self.em.account is not None:
            entries.append(_write_boc("em1_account", self.em.account.to_cell()))
        if self.em2 is not None and self.em2.account is not None:
            entries.append(_write_boc("em2_account", self.em2.account.to_cell()))

        meta = {
            "account_hex": account_hex,
            "account_addr": account_addr,
            "lt": lt,
            "now": now,
            "is_tock": is_tock,
            "tx_hash": tx_hash,
            "missing_em1": bool(missing_em1),
            "missing_em2": bool(missing_em2),
            "files": entries,
        }

        meta_path = os.path.join(dump_dir, f"{base_name}_meta.json")
        try:
            with open(meta_path, "w") as f:
                json.dump(meta, f, ensure_ascii=True)
            if self.loglevel > 2:
                logger.debug(f"Account-fail dump saved: {meta_path}")
        except Exception as e:
            logger.warning(f"Failed to write account-fail meta {meta_path!r}: {e}")

    def _compare_and_color(self, tx: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
        out: List[Dict[str, Any]] = []
        go_as_success = True
        account_code_hash = self._extract_account_code_hash()
        if self.em is None or self.em.transaction is None or self.em2 is None or self.em2.transaction is None:
            tx1_tlb = Transaction().cell_unpack(tx['tx'], True).dump()
            go_as_success = False
            err = {'mode': 'error', 'expected': tx['tx'].get_hash(), 'address': tx1_tlb['account_addr'],
                   'cant_emulate': True, 'fail_reason': 'emulation_new_failed', 'account_code_hash': account_code_hash}
            if self.em2 is not None and self.em2.transaction is not None:
                err['unchanged_emulator_tx_hash'] = self.em2.transaction.get_hash()
            else:
                err['cant_emulate_em2'] = True

            out.append(err)
            return go_as_success, out
        if self.em.account is None or self.em2.account is None:
            tx1_tlb = Transaction().cell_unpack(tx['tx'], True).dump()
            go_as_success = False
            s1_b64, s1_err, s1_type = self._state_to_boc_info(self.state1)
            s2_b64, s2_err, s2_type = self._state_to_boc_info(self.state2)
            err = {
                'mode': 'error',
                'expected': tx['tx'].get_hash(),
                'got': self.em.transaction.get_hash(),
                'address': tx1_tlb['account_addr'],
                'fail_reason': 'emulator_account_missing',
                'account_code_hash': account_code_hash,
                'missing_em1': self.em.account is None,
                'missing_em2': self.em2.account is None,
                'state1_boc_b64': s1_b64,
                'state1_boc_error': s1_err,
                'state1_type': s1_type,
                'state2_boc_b64': s2_b64,
                'state2_boc_error': s2_err,
                'state2_type': s2_type,
            }
            out.append(err)
            return go_as_success, out

        if self.em.transaction.get_hash() != tx['tx'].get_hash():
            # Dump emulated tx BOCs for debugging mismatches
            try:
                self._dump_post_emulation(tx, tx.get('lt'), tx.get('now'))
            except Exception as e:
                logger.warning(f"Failed to dump post-emulation tx bocs: {e}")
            diff, address = get_diff(tx['tx'], self.em.transaction.to_cell(), to_boc=self.use_boc_for_diff)

            unchanged_emulator_tx_hash = self.em2.transaction.get_hash()
            sa_diff = get_shard_account_diff(self.em2.account.to_cell(), self.em.account.to_cell())
            unchanged_emulator_tx_hash_match = unchanged_emulator_tx_hash == tx['tx'].get_hash()
            unchanged_emulator_account_hash = self.em2.account.to_cell().get_hash()
            changed_emulator_account_hash = self.em.account.to_cell().get_hash()

            account_diff_dict: Optional[Dict[str, Any]] = None
            if sa_diff is not None:
                account_diff_dict = {
                    'data': make_json_dumpable(sa_diff.to_dict())
                }
                if self.color_schema is not None and 'account' in self.color_schema:
                    acc_level, acc_log = get_colored_diff(sa_diff, self.color_schema, root='account')
                    account_diff_dict['acc_level'] = acc_level
                    account_diff_dict['acc_log'] = acc_log

            if self.color_schema is None:
                diff_dict = diff.to_dict()
                go_as_success = False
                err_obj: Dict[str, Any] = {'mode': 'error', 'diff': {
                    'transaction': make_json_dumpable(diff_dict),
                    'account': account_diff_dict.get('data', None) if isinstance(account_diff_dict, dict) else None,
                },
                                           'address': f"{self.block['block_id'].id.workchain}:{address}",
                                           'expected': tx['tx'].get_hash(), 'got': self.em.transaction.get_hash(),
                                           'fail_reason': 'hash_missmatch',
                                           'account_code_hash': account_code_hash}
                if account_diff_dict is not None:
                    err_obj['account_diff'] = account_diff_dict
                if unchanged_emulator_tx_hash is not None:
                    err_obj['unchanged_emulator_tx_hash'] = hex_to_b64(unchanged_emulator_tx_hash)
                if unchanged_emulator_tx_hash_match is not None:
                    err_obj['unchanged_emulator_tx_hash_match'] = unchanged_emulator_tx_hash_match
                if unchanged_emulator_account_hash is not None:
                    err_obj['unchanged_emulator_account_hash'] = unchanged_emulator_account_hash
                if changed_emulator_account_hash is not None:
                    err_obj['changed_emulator_account_hash'] = changed_emulator_account_hash
                out.append(err_obj)
            else:
                max_level, log = get_colored_diff(diff, self.color_schema)
                if account_diff_dict is not None and 'account' in self.color_schema:
                    try:
                        acc_level, acc_log = account_diff_dict.get('acc_level'), account_diff_dict.get('acc_log')
                        level_rank = {'alarm': 2, 'warn': 1, 'skip': 0}
                        if level_rank.get(acc_level, 0) > level_rank.get(max_level, 0):
                            max_level = acc_level
                        log = {**log, 'account': acc_log}
                    except Exception as e:
                        logger.error(f"ACCOUNT COLOR SCHEMA ERROR: {e}")

                address_str = f"{self.block['block_id'].id.workchain}:{address}"
                if max_level == 'alarm':
                    go_as_success = False
                    diff_dict = diff.to_dict()
                    err_obj = {'mode': 'error', 'diff': {
                        'transaction': make_json_dumpable(diff_dict),
                        'account': account_diff_dict.get('data', None) if isinstance(account_diff_dict, dict) else None,
                    }, 'address': address_str,
                               'expected': tx['tx'].get_hash(), 'got': self.em.transaction.get_hash(),
                               'color_schema_log': log,
                               'fail_reason': 'color_schema_alarm',
                               'account_code_hash': account_code_hash}

                    if account_diff_dict is not None:
                        err_obj['account_diff'] = account_diff_dict
                    if unchanged_emulator_tx_hash is not None:
                        err_obj['unchanged_emulator_tx_hash'] = hex_to_b64(unchanged_emulator_tx_hash)
                    if unchanged_emulator_tx_hash_match is not None:
                        err_obj['unchanged_emulator_tx_hash_match'] = unchanged_emulator_tx_hash_match
                    if unchanged_emulator_account_hash is not None:
                        err_obj['unchanged_emulator_account_hash'] = unchanged_emulator_account_hash
                    if changed_emulator_account_hash is not None:
                        err_obj['changed_emulator_account_hash'] = changed_emulator_account_hash
                    out.append(err_obj)
                elif max_level == 'warn':
                    go_as_success = False
                    # Include diff for warning the same way as for error to aid diagnostics
                    diff_dict = diff.to_dict()
                    warn_obj: Dict[str, Any] = {
                        'mode': 'warning',
                        'diff': {
                            'transaction': make_json_dumpable(diff_dict),
                            'account': account_diff_dict.get('data', None) if isinstance(account_diff_dict,
                                                                                         dict) else None,
                        },
                        'account_code_hash': account_code_hash,
                    }

                    if account_diff_dict is not None:
                        warn_obj['account_diff'] = account_diff_dict
                    if unchanged_emulator_tx_hash is not None:
                        warn_obj['unchanged_emulator_tx_hash'] = hex_to_b64(unchanged_emulator_tx_hash)
                    if unchanged_emulator_tx_hash_match is not None:
                        warn_obj['unchanged_emulator_tx_hash_match'] = unchanged_emulator_tx_hash_match
                    if unchanged_emulator_account_hash is not None:
                        warn_obj['unchanged_emulator_account_hash'] = unchanged_emulator_account_hash
                    if changed_emulator_account_hash is not None:
                        warn_obj['changed_emulator_account_hash'] = changed_emulator_account_hash
                    out.append(warn_obj)
        return go_as_success, out

    def _maybe_extract_out_msgs(self, extract_out_msgs: bool) -> Optional[Dict[str, Any]]:
        if extract_out_msgs and self.em is not None and self.em.transaction is not None:
            return extract_message_info(self.em.transaction.to_cell())
        return None

    def _extract_vm_log(self, em: EmulatorExtern) -> Optional[str]:
        if em is None:
            return None
        for attr in ("vm_log", "last_vm_log", "_vm_log"):
            try:
                val = getattr(em, attr, None)
                if isinstance(val, str) and val:
                    return val
            except Exception:
                continue
        for attr in ("last_result", "result", "_last_result", "emulation_result"):
            try:
                val = getattr(em, attr, None)
                if isinstance(val, dict):
                    vm_log = val.get("vm_log")
                    if isinstance(vm_log, str) and vm_log:
                        return vm_log
            except Exception:
                continue
        try:
            tx = getattr(em, "transaction", None)
            if tx is not None:
                vm_log = getattr(tx, "vm_log", None)
                if isinstance(vm_log, str) and vm_log:
                    return vm_log
        except Exception:
            pass
        return None

    def _maybe_log_vm(self, em: EmulatorExtern, tag: str, lt: int, now: int) -> None:
        if _get_env_int("EMULATOR_VM_LOG_PRINT", 0) <= 0:
            return
        vm_log = self._extract_vm_log(em)
        if not vm_log:
            return
        max_chars = _get_env_int("EMULATOR_VM_LOG_MAX", 0)
        if max_chars > 0 and len(vm_log) > max_chars:
            vm_log = vm_log[:max_chars] + "\n...[truncated]...\n"
        logger.info(f"VM log [{tag}] lt={lt} now={now}:\n{vm_log}")

    def emulate(
            self,
            tx: Dict[str, Any],
            override_in_msg: Optional[Cell] = None,
            extract_out_msgs: bool = False
    ) -> Tuple[List[Dict[str, Any]], Cell, Optional[Cell], Optional[Dict[str, Any]]]:
        # Prepare
        orig_in_msg, lt, now, is_tock = self._prepare_in_msg(tx)

        if self.loglevel > 4:
            if override_in_msg is None:
                logger.debug(f"Start tx: {lt}, {now}, {is_tock}")
            else:
                logger.debug(f"Start tx (override in_msg): {lt}, {now}")

        # Optional pre-emulation dump for diagnostics
        self._dump_pre_emulation(tx, orig_in_msg, override_in_msg, lt, now, is_tock)
        # Optional master proof dump for the block
        self._dump_master_proof(lt, now)

        # Primary
        success1 = self._run_primary(override_in_msg if override_in_msg is not None else orig_in_msg,
                                     now, lt, is_tock)
        # Secondary (always)
        success2 = self._run_secondary(orig_in_msg, now, lt, is_tock)

        self._maybe_log_vm(self.em, "em1" + ("-override" if override_in_msg is not None else ""), lt, now)
        self._maybe_log_vm(self.em2, "em2", lt, now)

        if self.loglevel > 4:
            logger.debug(
                f"Run success(em1{('-override' if override_in_msg is not None else '')}): {self.state1.get_hash() if self.state1 is not None else None} -> {success1}, TX: {self.em.transaction if self.em is not None else None}; (em2): {self.state2.get_hash() if self.state2 is not None else None} -> {success2}, TX: {self.em2.transaction if self.em2 is not None else None}")

        # Compare/hash/color (always using em2)
        go_as_success, out = self._compare_and_color(tx)

        # Finalize states
        acc1 = self.em.account if self.em is not None else None
        acc2 = self.em2.account if self.em2 is not None else None
        missing_accounts = (acc1 is None) or (acc2 is None)
        if missing_accounts:
            # Optional dump of BOCs when emulator account is missing
            self._dump_account_read_failure(
                tx,
                orig_in_msg,
                override_in_msg,
                lt,
                now,
                is_tock,
                missing_em1=acc1 is None,
                missing_em2=acc2 is None
            )
            state1_b64, state1_err, state1_type = self._state_to_boc_info(self.state1)
            state2_b64, state2_err, state2_type = self._state_to_boc_info(self.state2)
            logger.error(
                f"Emulator account missing: em1={acc1 is None} em2={acc2 is None} "
                f"state1_type={state1_type} state1_boc_b64={state1_b64} state1_boc_error={state1_err} "
                f"state2_type={state2_type} state2_boc_b64={state2_b64} state2_boc_error={state2_err}"
            )
            go_as_success = False
            has_missing_error = any(
                isinstance(e, dict) and e.get('fail_reason') == 'emulator_account_missing'
                for e in out
            )
            if not has_missing_error:
                try:
                    tx1_tlb = Transaction().cell_unpack(tx['tx'], True).dump()
                    address = tx1_tlb.get('account_addr')
                except Exception:
                    address = None
                err = {
                    'mode': 'error',
                    'expected': tx['tx'].get_hash(),
                    'fail_reason': 'emulator_account_missing',
                    'account_code_hash': self._extract_account_code_hash(),
                    'missing_em1': acc1 is None,
                    'missing_em2': acc2 is None,
                    'state1_boc_b64': state1_b64,
                    'state1_boc_error': state1_err,
                    'state1_type': state1_type,
                    'state2_boc_b64': state2_b64,
                    'state2_boc_error': state2_err,
                    'state2_type': state2_type,
                }
                if address is not None:
                    err['address'] = address
                if self.em is not None and self.em.transaction is not None:
                    err['got'] = self.em.transaction.get_hash()
                out.append(err)

        new_state_em1 = acc1.to_cell() if acc1 is not None else self.state1
        new_state_em2 = acc2.to_cell() if acc2 is not None else self.state2
        # Update internal states for subsequent calls when this instance is reused
        self.state1 = new_state_em1
        self.state2 = new_state_em2

        if go_as_success:
            out.append({'mode': 'success', 'account_code_hash': self._extract_account_code_hash()})

        out_msgs = self._maybe_extract_out_msgs(extract_out_msgs)
        return out, new_state_em1, new_state_em2, out_msgs
