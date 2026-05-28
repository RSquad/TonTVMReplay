#!/usr/bin/env python3
"""Continuously spawn tonemuso validators over 10-seqno windows of the TON masterchain."""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WINDOW = 15
POLL_INTERVAL = 3 # seconds between seqno checks
MAX_PARALLEL = 24
TONCENTER_URL = os.environ.get(
    "TONCENTER_URL", "https://toncenter.com/api/v2/getMasterchainInfo"
)
TONCENTER_API_KEY = os.environ.get("TONCENTER_API_KEY")
LOG_DIR = Path(os.environ.get("BATCH_RUNNER_LOG_DIR", "logs"))
EXIT_LOG = LOG_DIR / "tonemuso_exits.jsonl"
SKIP_LOG = LOG_DIR / "tonemuso_skipped.jsonl"

live = {}  # pid -> (from_seqno, to_seqno, started_at)
shutdown = False

def log_jsonl(path, record):
    record["ts"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")

def get_tip():
    try:
        req = urllib.request.Request(TONCENTER_URL)
        if TONCENTER_API_KEY:
            req.add_header("X-API-Key", TONCENTER_API_KEY)
        with urllib.request.urlopen(req, timeout=10) as r:
            return int(json.loads(r.read())["result"]["last"]["seqno"])
    except Exception as e:
        print(f"[warn] tip fetch failed: {e}", flush=True)
        return None

def spawn(frm, to):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"run_{frm}_{to}.log"
    env = {**os.environ, "FROM_SEQNO": str(frm), "TO_SEQNO": str(to)}
    log_fh = open(log_path, "wb")
    p = subprocess.Popen(
        ["tonemuso"], env=env, stdout=log_fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    live[p.pid] = (frm, to, time.time(), p, log_fh)
    print(f"[spawn] pid={p.pid} {frm}..{to} (live={len(live)})", flush=True)

def reap():
    for pid in list(live.keys()):
        frm, to, started, p, log_fh = live[pid]
        rc = p.poll()
        if rc is not None:
            log_fh.close()
            duration = round(time.time() - started, 2)
            log_jsonl(EXIT_LOG, {"pid": pid, "from": frm, "to": to,
                                 "exit_code": rc, "duration_s": duration})
            print(f"[exit ] pid={pid} {frm}..{to} rc={rc} {duration}s", flush=True)
            del live[pid]

def handle_signal(signum, _frame):
    global shutdown
    print(f"[sig  ] {signum} received, stopping spawns; waiting for {len(live)} children", flush=True)
    shutdown = True

def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    last = None
    while last is None:
        last = get_tip()
        if last is None:
            time.sleep(POLL_INTERVAL)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[start] writing logs to {LOG_DIR}", flush=True)
    print(f"[start] resuming from current tip seqno={last}", flush=True)

    while not shutdown:
        reap()
        tip = get_tip()
        if tip is not None:
            while tip - last >= WINDOW:
                frm, to = last, last + WINDOW
                if len(live) >= MAX_PARALLEL:
                    log_jsonl(SKIP_LOG, {"from": frm, "to": to, "reason": "max_parallel", "live": len(live)})
                    print(f"[skip ] {frm}..{to} (cap {MAX_PARALLEL} reached)", flush=True)
                else:
                    spawn(frm, to)
                last = to
        time.sleep(POLL_INTERVAL)

    while live:
        reap()
        time.sleep(0.5)
    print("[done ] all children exited", flush=True)

if __name__ == "__main__":
    main()
