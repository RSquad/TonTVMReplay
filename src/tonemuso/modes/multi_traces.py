from tonemuso.toncenter_api import ToncenterAPI
from tonemuso.toncenter_models import TonTrace
from queue import Empty as QueueEmpty
import time as time_module
from typing import List, Tuple, Dict, Any

from tonpy.blockscanner.blockscanner import *
from tonpy.autogen.block import BlockId, Block, BlockInfo

from tonemuso.config import Config
from tonemuso.modes.common import collect_raw, worker_init, process_one_trace_worker, build_preindex, cleanup_scanner
from tonemuso.utils import count_modes_tree


def run_scanner_with_failover(cfg: Config, lcparams: dict, from_seqno: int, to_seqno: int, 
                               max_wait_time: int = 300) -> List[Tuple[Dict, Cell, List]]:
    """
    Run BlockScanner with automatic failover to next liteserver on timeout.
    
    Args:
        cfg: Configuration object
        lcparams: LiteClient parameters with all servers
        from_seqno: Start seqno
        to_seqno: End seqno
        max_wait_time: Maximum wait time in seconds before trying next server
    
    Returns:
        List of raw chunks from successful scan
    """
    servers = cfg.liteclient_servers()
    num_servers = len(servers)
    
    if num_servers <= 1:
        logger.warning("Only one liteserver configured, no failover available")
        max_retries = 1
    else:
        logger.info(f"Running with failover across {num_servers} liteservers")
        max_retries = num_servers
    
    for attempt in range(max_retries):
        if num_servers > 1:
            # Rotate servers: use different starting server for each attempt
            rotated_servers = servers[attempt:] + servers[:attempt]
            current_lcparams = dict(lcparams)
            current_lcparams['my_rr_servers'] = rotated_servers
            server_info = f"{rotated_servers[0]['ip']}:{rotated_servers[0]['port']}"
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: Using liteserver starting with {server_info}")
        else:
            current_lcparams = lcparams
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: Using single liteserver")
        
        outq = Queue()
        scanner = BlockScanner(
            lcparams=current_lcparams,
            start_from=from_seqno,
            load_to=to_seqno,
            nproc=int(cfg.nproc),
            loglevel=cfg.loglevel,
            chunk_size=int(cfg.chunk_size),
            tx_chunk_size=int(cfg.tx_chunk_size),
            raw_process=collect_raw(config_override=cfg.c7_rewrite, loglevel=cfg.loglevel, 
                                   emulator_path=cfg.emulator_path,
                                   emulator_unchanged_path=cfg.emulator_unchanged_path, 
                                   trace_tx_hashes_hex=set()),
            out_queue=outq,
            only_mc_blocks=bool(cfg.only_mc_blocks),
            parse_txs_over_ls=True,
            blocks_to_load=None
        )
        
        scanner.start()

        try:
            raw_chunks_all = []
            start_time = time_module.time()
            last_progress_time = start_time
            last_chunk_count = 0
            
            while not scanner.done:
                # Check for timeout
                elapsed = time_module.time() - start_time
                if elapsed > max_wait_time:
                    logger.error(f"Scanner timeout after {elapsed:.1f}s on liteserver attempt {attempt + 1}")
                    # Stop scanner (it might be hanging in workers/threads)
                    cleanup_scanner(scanner, outq, stop=True, join_timeout=5.0)
                    break
                
                # Collect chunks
                collected_any = False
                while True:
                    try:
                        raw_chunk = outq.get_nowait()
                        raw_chunks_all.extend(raw_chunk)
                        collected_any = True
                    except QueueEmpty:
                        break
                
                # Check for progress
                if collected_any:
                    last_progress_time = time_module.time()
                    last_chunk_count = len(raw_chunks_all)
                else:
                    # No progress for too long, may be stuck
                    time_since_progress = time_module.time() - last_progress_time
                    if time_since_progress > 120:  # 2 minutes without progress
                        logger.error(f"No progress for {time_since_progress:.1f}s, scanner may be stuck")
                        break
                
                sleep(1)
            
            # Final collection
            while True:
                try:
                    raw_chunk = outq.get_nowait()
                    raw_chunks_all.extend(raw_chunk)
                except QueueEmpty:
                    break
            
            # Check if we got results
            if scanner.done and raw_chunks_all:
                logger.info(f"Successfully loaded {len(raw_chunks_all)} chunks")
                cleanup_scanner(scanner, outq, stop=False)
                return raw_chunks_all
            elif raw_chunks_all:
                logger.warning(f"Scanner incomplete but got {len(raw_chunks_all)} chunks, may be partial")
                # If we have some data and this is the last attempt, use it
                if attempt == max_retries - 1:
                    logger.warning("Last attempt, using partial data")
                    cleanup_scanner(scanner, outq, stop=False)
                    return raw_chunks_all
            else:
                logger.error(f"Scanner failed to load any data on attempt {attempt + 1}")
        
        except Exception as e:
            logger.error(f"Scanner monitoring error on attempt {attempt + 1}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        # Cleanup scanner and queue before next retry
        cleanup_scanner(scanner, outq, stop=True, join_timeout=5.0)
        
        # If not the last attempt, continue to next server
        if attempt < max_retries - 1:
            logger.warning(f"Retrying with next liteserver...")
            sleep(2)  # Brief pause before retry
    
    # All attempts failed
    raise RuntimeError(f"Failed to load blocks after {max_retries} attempts across all liteservers")


def run(cfg: Config):
    lcparams = cfg.lcparams()
    lc = LiteClient(**lcparams)

    to_seqno = int(cfg.to_seqno or lc.get_masterchain_info_ext().last.id.seqno)
    from_seqno = cfg.from_seqno or (to_seqno - int(cfg.to_emulate_mc_blocks))

    # Resolve LTs
    start_blk_id_ext = lc.lookup_block(BlockId(-1, 0x8000000000000000, int(from_seqno))).blk_id
    end_blk_id_ext = lc.lookup_block(BlockId(-1, 0x8000000000000000, int(to_seqno))).blk_id

    start_blk_cell = lc.get_block(start_blk_id_ext)
    end_blk_cell = lc.get_block(end_blk_id_ext)

    start_blk = Block().cell_unpack(start_blk_cell)
    end_blk = Block().cell_unpack(end_blk_cell)

    start_info = BlockInfo().cell_unpack(start_blk.info, True)
    end_info = BlockInfo().cell_unpack(end_blk.info, True)

    start_lt = int(start_info.start_lt)
    end_lt = int(end_info.end_lt)

    api = ToncenterAPI(base_url=cfg.toncenter_api, api_key=cfg.toncenter_api_key, timeout=60)

    all_traces = []
    offset = 0
    limit = 1000
    downloaded = 0
    pages = 0
    pbar = tqdm(total=None, unit="tr", desc="Downloading traces", disable=False)

    num_run = 0
    while True:
        try:
            traces_page = api.get_traces_by_lt_range_typed(start_lt=start_lt, end_lt=end_lt, include_actions=False, limit=limit, offset=offset, sort='desc')
        except Exception as e:
            logger.error(f"Failed to query toncenter traces page offset={offset}: {e}")
            if num_run > 10:
                raise ValueError(f"Can't get traces from toncenter for given LT range")
            else:
                num_run += 1
                continue
        page_traces = traces_page or []
        if not page_traces:
            break
        all_traces.extend(page_traces)
        downloaded += len(page_traces)
        pages += 1
        pbar.update(len(page_traces))
        offset += limit
    pbar.close()

    logger.debug(
        f"Toncenter multi-trace fetch complete: total={len(all_traces)}, pages={pages}, downloaded={downloaded}, lt_range=[{start_lt},{end_lt}]")
    if not all_traces:
        logger.error("No traces returned from toncenter for given LT range")
        return

    # Union of blocks
    union_blocks = set()
    for trace in all_traces:
        try:
            for _, txd in trace.transactions.items():
                bref = txd.block_ref
                if bref is not None:
                    union_blocks.add((int(bref.workchain), int(bref.shard), int(bref.seqno)))

        except Exception as e:
            logger.error(f"Failed to collect blocks for a trace: {e}")

    blocks_to_load_all = []
    for wc, shard_int, seq in sorted(union_blocks):
        try:
            blk = lc.lookup_block(BlockId(wc, shard_int, seq)).blk_id
            blocks_to_load_all.append(blk)
        except Exception as e:
            logger.error(f"Failed to lookup block ({wc},{hex(shard_int)},{seq}): {e}")

    if not blocks_to_load_all:
        logger.error("No blocks resolved to load for the collected traces")
        return

    # Scan with automatic failover
    try:
        raw_chunks_all = run_scanner_with_failover(
            cfg=cfg,
            lcparams=lcparams,
            from_seqno=from_seqno,
            to_seqno=to_seqno,
            max_wait_time=300  # 5 minutes timeout per liteserver attempt
        )
    except RuntimeError as e:
        logger.error(f"Failed to load blocks: {e}")
        return

    preindex = build_preindex(raw_chunks_all)

    # Run per-trace in pool
    max_workers = int(cfg.nproc)
    aggregated_failed: list = []
    pbar2 = tqdm(total=len(all_traces), desc="Emulating traces", unit="trace", disable=False)

    ctx = get_context("spawn")
    c7_env = cfg.c7_rewrite_raw
    args_iter = list(enumerate(all_traces))
    with ctx.Pool(processes=max_workers, initializer=worker_init,
                  initargs=(preindex, lcparams, cfg.loglevel, cfg.color_schema, c7_env, cfg.emulator_path, cfg.emulator_unchanged_path)) as pool:
        for res in pool.imap_unordered(process_one_trace_worker, args_iter):
            if res:
                aggregated_failed.extend(res)
            pbar2.update(1)

    if aggregated_failed:
        import json
        with open("failed_traces.json", "w") as f:
            json.dump(aggregated_failed, f)

    total_success = total_warnings = total_unsuccess = total_new = total_missed = 0
    trace_success_count = trace_warning_count = trace_unsuccess_count = 0
    for entry in aggregated_failed or []:
        emu = entry.get('emulated_trace') or {}
        c = count_modes_tree(emu)
        total_success += c.get('success', 0)
        total_warnings += c.get('warnings', 0)
        total_unsuccess += c.get('unsuccess', 0)
        total_new += c.get('new', 0)
        total_missed += c.get('missed', 0)
        not_presented = entry.get('not_presented') or []
        total_missed += len(not_presented)
        fstatus = (entry.get('final_status') or '').lower()
        if fstatus == 'success':
            trace_success_count += 1
        elif fstatus == 'warning' or fstatus == 'warnings':
            trace_warning_count += 1
        else:
            trace_unsuccess_count += 1
    logger.warning(
        f"Final emulator status: {total_success} success, {total_unsuccess} unsuccess, {total_warnings} warnings, {total_new} new, {total_missed} missed; traces: {trace_success_count} success, {trace_warning_count} warnings, {trace_unsuccess_count} unsuccess")
