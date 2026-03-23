import json as std_json
from collections import Counter
from loguru import logger
from tonpy.blockscanner.blockscanner import *

from tonemuso.config import Config
from tonemuso.modes.common import process_blocks, process_result, cleanup_scanner
from tonemuso.debug_dumper import init_dumper, get_dumper


def run(cfg: Config):
    dumper = init_dumper(cfg.debug_dumps_dir)
    debug_dumps_run_dir = dumper.run_dir if dumper else None

    txs_whitelist = None
    txs_list = None
    if cfg.txs_to_process and isinstance(cfg.txs_to_process, dict):
        txs_list = cfg.txs_to_process.get('transactions') or []
        txs_whitelist = set(t['hash'] for t in txs_list)

    lcparams = cfg.lcparams()
    lc = LiteClient(**lcparams)

    # Build blocks_to_load from tx list
    blocks_to_load = []
    known = set()
    for tx in txs_list or []:
        if tx['root_hash'] in known:
            continue
        blocks_to_load.append(BlockIdExt(BlockId(workchain=tx['workchain'], shard=tx['shard'], seqno=tx['seqno']),
                                         root_hash=tx['root_hash'], file_hash=tx['file_hash']))
        known.add(tx['root_hash'])

    outq = Queue()
    raw_proc = process_blocks(config_override=cfg.c7_rewrite, trace_whitelist=None, loglevel=cfg.loglevel,
                              color_schema=cfg.color_schema, emulator_path=cfg.emulator_path,
                              emulator_unchanged_path=cfg.emulator_unchanged_path, txs_whitelist=txs_whitelist,
                              debug_dumps_run_dir=debug_dumps_run_dir)

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
        blocks_to_load=blocks_to_load
    )

    scanner.start()

    success = 0
    warnings = []
    unsuccess = []
    unique_accounts = set()

    try:
        while not scanner.done:
            tmp_s, tmp_u, tmp_w, tmp_addrs = process_result(outq, loglevel=cfg.loglevel)
            success += tmp_s
            warnings.extend(tmp_w)
            unsuccess.extend(tmp_u)
            unique_accounts.update(tmp_addrs)
            sleep(1)

        tmp_s, tmp_u, tmp_w, tmp_addrs = process_result(outq, loglevel=cfg.loglevel)
        success += tmp_s
        warnings.extend(tmp_w)
        unsuccess.extend(tmp_u)
        unique_accounts.update(tmp_addrs)
    finally:
        cleanup_scanner(scanner, outq, stop=True)

    logger.warning(f"Final emulator status: {success} success, {len(unsuccess)} unsuccess, {warnings} warnings")
    logger.warning(f"Total unique accounts processed: {len(unique_accounts)}")
    
    if unsuccess:
        cnt = Counter()
        for i in unsuccess:
            cnt[i['address']] += 1
        logger.error(f"Unique addreses errors: {len(cnt)}, most common: ")
        logger.error(cnt.most_common(5))
        import json as std_json
        with open("failed_txs.json", "w") as f:
            std_json.dump(unsuccess, f, indent=2)
    if warnings:
        import json as std_json
        import os
        dumper = get_dumper()
        warnings_path = os.path.join(dumper.run_dir, "warnings.json") if dumper else "warnings.json"
        with open(warnings_path, "w") as f:
            std_json.dump(warnings, f, indent=2)
        logger.info(f"Warnings saved to: {warnings_path}")
