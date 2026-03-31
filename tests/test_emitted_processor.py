import os
import sys
import types
import unittest
from collections import OrderedDict, defaultdict
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from tonemuso.emitted_processor import EmittedChildCandidate, EmittedMessageProcessor
from tonemuso.trace_models import TxRecord


class FakeAddress:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    def __eq__(self, other) -> bool:
        return isinstance(other, FakeAddress) and self.raw == other.raw

    def __hash__(self) -> int:
        return hash(self.raw)

    def __str__(self) -> str:
        return self.raw


class FakeTransactionParser:
    def cell_unpack(self, _cell, _allow_special):
        # account_addr is read as a binary string and converted into wc:addr hex form.
        return types.SimpleNamespace(account_addr="1")


class FakeStepEmulator:
    seen_states = []

    def __init__(
        self,
        *,
        block,
        loglevel,
        color_schema,
        em,
        account_state_em1,
        em2,
        account_state_em2,
        use_boc_for_diff,
    ) -> None:
        del block, loglevel, color_schema, em, em2, account_state_em2, use_boc_for_diff
        FakeStepEmulator.seen_states.append(account_state_em1)

    def emulate(self, tx, override_in_msg=None, extract_out_msgs=True):
        del tx, override_in_msg, extract_out_msgs
        return [], "new-state", None, {"out_msgs": []}


class FakeRunner:
    def __init__(self, *, state_source: str) -> None:
        self.block_key = (0, 0, 0, 0)
        self.parent_hash = "PARENT"
        self.child_hash = "CHILD"
        self.expected_in_b64 = "expected-in"
        self.account_addr = FakeAddress("0:0000000000000000000000000000000000000000000000000000000000000001")

        child_tx = TxRecord(tx="child-cell", lt=2, now=3, is_tock=False)
        child_tx.before_txs = [types.SimpleNamespace(tx="buffered-tx")]

        self.tx_index = {self.child_hash: (self.block_key, child_tx)}
        self.child_map = {self.parent_hash: [self.expected_in_b64]}
        self.child_link_map = {self.parent_hash: {self.expected_in_b64: self.child_hash}}
        self.blocks = {self.block_key: object()}
        self.loglevel = 0
        self.color_schema = None
        self.use_boc_for_diff = False
        self.used_original_pairs = set()
        self.message_meta = {}
        self._emulation_order = 0

        self.before_states = OrderedDict()
        self.account_states1 = defaultdict(OrderedDict)
        self.default_initial_state = defaultdict(OrderedDict)

        if state_source == "before_states":
            self.before_states[self.child_hash] = "before-state"
        elif state_source == "account_states1":
            self.account_states1[self.block_key][self.account_addr] = "account-state"
        else:
            raise AssertionError(f"unexpected state source: {state_source}")

    def _get_emulators(self, block_key):
        assert block_key == self.block_key
        return object(), object()

    def _fetch_state_for_account(self, block_key, account_addr):
        raise AssertionError(f"_fetch_state_for_account should not be used: {block_key}, {account_addr}")

    def _next_emulation_order(self) -> int:
        self._emulation_order += 1
        return self._emulation_order


def make_candidate(child_hash: str, expected_in_b64: str) -> EmittedChildCandidate:
    emu_msg = types.SimpleNamespace(
        cell="override-msg-cell",
        bounce=None,
        bounced=None,
        opcode=None,
        dest=None,
        hash_b64="generated-in-msg",
        body_hash=None,
    )
    return EmittedChildCandidate(
        emu_msg=emu_msg,
        match=True,
        expected_in_b64=expected_in_b64,
        child_tx_hash_hex=child_hash,
        original_order_idx=0,
    )


class EmittedProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeStepEmulator.seen_states = []

    def test_skips_before_txs_when_state_comes_from_before_states(self) -> None:
        runner = FakeRunner(state_source="before_states")
        processor = EmittedMessageProcessor(runner)
        candidate = make_candidate(runner.child_hash, runner.expected_in_b64)

        with mock.patch("tonemuso.emitted_processor.Address", FakeAddress), mock.patch(
            "tonemuso.emitted_processor.Transaction", FakeTransactionParser
        ), mock.patch("tonemuso.emitted_processor.TxStepEmulator", FakeStepEmulator):
            processor._emulate_buffer_txs = mock.Mock(
                side_effect=AssertionError("_emulate_buffer_txs must not run for before_states")
            )

            processor.process_emitted_children_with_override(
                runner.block_key,
                runner.parent_hash,
                TxRecord(tx="parent-cell", lt=1, now=2, is_tock=False),
                [candidate],
                [],
                set(),
            )

        self.assertEqual(FakeStepEmulator.seen_states, ["before-state"])

    def test_pre_applies_before_txs_when_state_comes_from_account_states(self) -> None:
        runner = FakeRunner(state_source="account_states1")
        processor = EmittedMessageProcessor(runner)
        candidate = make_candidate(runner.child_hash, runner.expected_in_b64)

        with mock.patch("tonemuso.emitted_processor.Address", FakeAddress), mock.patch(
            "tonemuso.emitted_processor.Transaction", FakeTransactionParser
        ), mock.patch("tonemuso.emitted_processor.TxStepEmulator", FakeStepEmulator):
            processor._emulate_buffer_txs = mock.Mock(return_value=("buffered-state", 1))

            nodes, next_contexts = processor.process_emitted_children_with_override(
                runner.block_key,
                runner.parent_hash,
                TxRecord(tx="parent-cell", lt=1, now=2, is_tock=False),
                [candidate],
                [],
                set(),
            )

        processor._emulate_buffer_txs.assert_called_once()
        self.assertEqual(FakeStepEmulator.seen_states, ["buffered-state"])
        self.assertEqual(nodes[0]["buffer_emulated_count"], 1)
        self.assertEqual(nodes[0]["account_state_source"], "account_states1")
        self.assertEqual(len(next_contexts), 1)


if __name__ == "__main__":
    unittest.main()
