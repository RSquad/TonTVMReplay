import json as std_json
from loguru import logger
from queue import Empty as QueueEmpty
from tonpy.blockscanner.blockscanner import *
from tonpy.autogen.block import BlockId, Block

from tonemuso.config import Config
from tonemuso.modes.common import process_blocks, process_result, cleanup_scanner
from tonemuso.debug_dumper import init_dumper, get_dumper


def run(cfg: Config):
    dumper = init_dumper(cfg.debug_dumps_dir, mode=cfg.debug_dumps_mode)
    debug_dumps_run_dir = dumper.run_dir if dumper else None

    txs_whitelist = None
    if cfg.txs_to_process and isinstance(cfg.txs_to_process, dict):
        txs = cfg.txs_to_process.get('transactions') or []
        txs_whitelist = set(t['hash'] for t in txs)

    lcparams = cfg.lcparams()
    lc = LiteClient(**lcparams)
    latest_seqno = lc.get_masterchain_info_ext().last.id.seqno
    to_seqno = int(cfg.to_seqno or latest_seqno)
    from_seqno = cfg.from_seqno or (to_seqno - int(cfg.to_emulate_mc_blocks))

    outq = Queue()
    raw_proc = process_blocks(config_override=cfg.c7_rewrite, trace_whitelist=None, loglevel=cfg.loglevel, color_schema=cfg.color_schema, emulator_path=cfg.emulator_path, emulator_unchanged_path=cfg.emulator_unchanged_path, txs_whitelist=txs_whitelist, debug_dumps_run_dir=debug_dumps_run_dir)
    scanner = BlockScanner(
        lcparams=lcparams,
        start_from=from_seqno,
        load_to=to_seqno,
        nproc=int(cfg.nproc),
        loglevel=cfg.loglevel,
        chunk_size=int(cfg.chunk_size),
        tx_chunk_size=int(cfg.tx_chunk_size),
        raw_process=raw_proc,
        out_queue=outq,
        only_mc_blocks=bool(cfg.only_mc_blocks),
        parse_txs_over_ls=bool(cfg.parse_over_ls),
        blocks_to_load=None
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
        from collections import Counter
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
