#!/usr/bin/env python3
import argparse
import traceback

from tonpy.blockscanner.blockscanner import BlockId, LiteClient


MC_SHARD = 0x8000000000000000


def make_client(args):
    return LiteClient(
        host=args.host,
        port=args.port,
        pubkey_base64=args.pubkey,
        timeout=args.timeout,
    )


def resolve_key_seqno(lc: LiteClient, seqno: int) -> int:
    blk = lc.lookup_block(BlockId(-1, MC_SHARD, seqno)).blk_id
    print(f"lookup_block OK: {blk}")

    hdr = lc.get_block_header(blk)
    hdr_cell = hdr.virt_blk_root
    hdr_dump = hdr_cell.dump_as_tlb("BlockInfo")
    prev_key_seqno = int(hdr_dump["prev_key_block_seqno"])
    print(f"target block prev_key_block_seqno={prev_key_seqno}")
    return prev_key_seqno


def probe(lc: LiteClient, key_seqno: int) -> int:
    key_blk = lc.lookup_block(BlockId(-1, MC_SHARD, key_seqno)).blk_id
    print(f"key block id: {key_blk}")

    rc = 0
    for flag in (True, False):
        print(f"--- from_not_trusted_keyblock={flag} ---")
        try:
            blk_id, cfg = lc.get_config_all(key_blk, from_not_trusted_keyblock=flag)
            print(f"OK: blk_id={blk_id} cfg_type={type(cfg).__name__}")
        except Exception as exc:
            rc = 1
            print(f"FAIL: {exc!r}")
            traceback.print_exc()
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Probe get_config_all() on a specific liteserver."
    )
    ap.add_argument("--host", type=int, required=True, help="LS IPv4 as int")
    ap.add_argument("--port", type=int, required=True, help="LS port")
    ap.add_argument("--pubkey", required=True, help="LS pubkey in base64")
    ap.add_argument("--timeout", type=int, default=20, help="request timeout")
    ap.add_argument("--seqno", type=int, help="target MC block seqno")
    ap.add_argument("--key-seqno", type=int, help="key block seqno to probe directly")
    args = ap.parse_args()

    if args.seqno is None and args.key_seqno is None:
        ap.error("pass either --seqno or --key-seqno")

    lc = make_client(args)
    print("liteserver connected")

    key_seqno = args.key_seqno
    if key_seqno is None:
        key_seqno = resolve_key_seqno(lc, args.seqno)

    return probe(lc, key_seqno)


if __name__ == "__main__":
    raise SystemExit(main())
