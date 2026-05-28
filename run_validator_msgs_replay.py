#!/usr/bin/env python3
import json
from collections import Counter
from multiprocessing import Queue
from time import sleep

from loguru import logger
from tonpy.blockscanner.blockscanner import BlockId, BlockIdExt, BlockScanner

from tonemuso.config import Config
from tonemuso.debug_dumper import init_dumper
from tonemuso.modes.common import cleanup_scanner, process_blocks, process_result


# BLOCKS = [
#     (0, 9223372036854775808, 69280493,
#      "45e1706b1fb20112b3970249a474c92e55402032963cb3271e8f7589fa6c839a",
#      "62c9a55f72e0be6b7ef5c5c8c6a5c7dc3beb748a0a6dd1cc08a9824415ba64eb"),
#     (0, 9223372036854775808, 69280573,
#      "317055f4d4ded56306c688819ef1f9c8f31f2b0617f05d31c0bb12923f857b68",
#      "adde962be968960b4782d3d793b5fef13954d0fd7869acd5a83f750be99d7992"),
#     (0, 9223372036854775808, 69280661,
#      "673ed729ea0e9e681f0cd4606342d538effe3b5198ac7add5633eb925a5d0425",
#      "e4317b593fade318b472d79f42f498308512ac5fa8ad367c1a0726ce1b22828f"),
#     (0, 9223372036854775808, 69280662,
#      "c3078c331d11d243b0ee776ba3a645ce225931819c4ea1cc779c87e9f756d8a4",
#      "eea21e52f2c5015450a710198a09a012ec5bc9bbe7e5d3691fd159184fd295bb"),
#     (0, 9223372036854775808, 69280749,
#      "a779e8a2c64175f4a1a9b72137548ed563de126255cf05476b9f9834018aa6aa",
#      "fc5270f80f74df7a9e8a986da0010fb67d29c33b2b06e913b8ceefe2f00f9243"),
# ]
BLOCKS = [
    (0, 9223372036854775808, 69516475,
     "86f2d702544237372b81d02647391f98e2f0e6a61c1053e8d28ed8f91a8d374c",
     "9a15b331f45b60d10bb526d42056988fbb31540ec32538dcaeb44f489ace129b"),
]

INTERESTING = {
    ("0:72f283c79360f3c7f93d8f1f98d15e86af032143d3e3956eb6400558cd3b7f33", 74793118000019)
}


def main() -> int:
    cfg = Config.from_env()
    cfg.nproc = 1
    cfg.chunk_size = 1
    cfg.tx_chunk_size = 100
    cfg.loglevel = 3

    dumper = init_dumper(cfg.debug_dumps_dir, mode=cfg.debug_dumps_mode)
    run_dir = dumper.run_dir if dumper else None
    logger.warning(f"Validator msg replay dump dir: {run_dir}")

    blocks_to_load = [
        BlockIdExt(
            BlockId(workchain=wc, shard=shard, seqno=seqno),
            root_hash=root_hash.upper(),
            file_hash=file_hash.upper(),
        )
        for wc, shard, seqno, root_hash, file_hash in BLOCKS
    ]

    outq = Queue()
    raw_proc = process_blocks(
        config_override=cfg.c7_rewrite,
        trace_whitelist=None,
        loglevel=cfg.loglevel,
        color_schema=cfg.color_schema,
        emulator_path=cfg.emulator_path,
        emulator_unchanged_path=cfg.emulator_unchanged_path,
        txs_whitelist=None,
        debug_dumps_run_dir=run_dir,
    )
    scanner = BlockScanner(
        lcparams=cfg.lcparams(),
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

    scanner.start()
    success = 0
    warnings = []
    errors = []
    addrs = set()
    try:
        while not scanner.done:
            s, u, w, a = process_result(outq, loglevel=cfg.loglevel)
            success += s
            errors.extend(u)
            warnings.extend(w)
            addrs.update(a)
            sleep(1)
        s, u, w, a = process_result(outq, loglevel=cfg.loglevel)
        success += s
        errors.extend(u)
        warnings.extend(w)
        addrs.update(a)
    finally:
        cleanup_scanner(scanner, outq, stop=True)

    interesting_hits = []
    for entry in errors + warnings:
        addr = (entry.get("address") or "").upper()
        lt = entry.get("lt")
        if (addr, lt) in INTERESTING:
            interesting_hits.append(entry)

    payload = {
        "run_dir": run_dir,
        "success": success,
        "errors_count": len(errors),
        "warnings_count": len(warnings),
        "unique_accounts": len(addrs),
        "interesting_hits_count": len(interesting_hits),
        "interesting_hits": interesting_hits,
        "error_addresses_top": Counter(
            e.get("address") for e in errors if isinstance(e, dict)
        ).most_common(20),
    }
    with open("/tmp/validator_msgs_replay_result.json", "w") as f:
        json.dump(payload, f, indent=2)
    logger.warning(json.dumps({
        key: payload[key]
        for key in [
            "run_dir",
            "success",
            "errors_count",
            "warnings_count",
            "unique_accounts",
            "interesting_hits_count",
        ]
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
