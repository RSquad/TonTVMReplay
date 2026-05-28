cd /home/automation/alexey/TonTVMReplay
source my_venv/bin/activate

EMU_JSON=/home/automation/alexey/TonTVMReplay/debug_dumps_bundle_test/run_20260506_105716/failed/51EA15BF/emulator_test.json \
EMU_SO=/home/automation/alexey/TonTVMReplay/cpp/libemulator.so \
python3 - <<'PY'
import json, os
from ctypes import c_int, c_bool
from tonpy import Cell, VmDict
from tonpy.tvm.not_native.emulator_extern import EmulatorExtern

p = os.environ["EMU_JSON"]
so = os.environ["EMU_SO"]
t = json.load(open(p))

cfg = VmDict(32, False, Cell(t["config_params_boc"]))
libs = VmDict(256, False, cell_root=Cell(t["libs_boc"]))
acc = Cell(t["shard_account_boc"])
msg = None if not t.get("message_boc") else Cell(t["message_boc"])
expected_tx = Cell(t["tx_boc"])

try:
    em = EmulatorExtern(so, cfg, 20)
except TypeError:
    em = EmulatorExtern(so, cfg)

try:
    fn = em.libemulator.emulator_set_verbosity_level
    fn.argtypes = [c_int]
    fn.restype = c_bool
    print("set_verbosity:", fn(20))
except Exception as e:
    print("set_verbosity_error:", e)

em.set_rand_seed(t["rand_seed"])
em.set_prev_blocks_info(t["prev_blocks_info"])
em.set_libs(libs)

if msg is None:
    ok = em.emulate_tick_tock_transaction(acc, t["is_tock"], t["now"], t["lt"])
else:
    ok = em.emulate_transaction(acc, msg, t["now"], t["lt"])

got_tx = em.transaction.to_cell() if em.transaction else None
got_acc = em.account.to_cell() if em.account else None

print("emulator:", so)
print("ok:", ok)
print("expected_tx_hash:", expected_tx.get_hash())
print("got_tx_hash     :", got_tx.get_hash() if got_tx else None)
print("match:", (got_tx.get_hash() == expected_tx.get_hash()) if got_tx else False)
print("account_hash:", got_acc.get_hash() if got_acc else None)
PY
