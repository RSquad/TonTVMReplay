#!/usr/bin/env python3
import json
import html
import os
import signal
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
STATE_PATH = REPORTS_DIR / "runs.json"
ENV_PATH = ROOT / ".env"
DEBUG_DUMPS_DIR = ROOT / "debug_dumps"
RUN_LOG_PATH = ROOT / "cycle_run.log"

DEFAULT_RANGE_SIZE = int(os.getenv("CYCLE_RANGE_SIZE", "1000"))
DEFAULT_RUN_TIMEOUT_SEC = int(os.getenv("CYCLE_RUN_TIMEOUT_SEC", "7200"))
DEFAULT_RESTART_DELAY_SEC = int(os.getenv("CYCLE_RESTART_DELAY_SEC", "15"))
DEFAULT_MAX_RESTARTS = int(os.getenv("CYCLE_MAX_RESTARTS", "3"))
DEFAULT_POLL_SEC = int(os.getenv("CYCLE_POLL_SEC", "60"))
DEFAULT_HOST = os.getenv("CYCLE_WEB_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("CYCLE_WEB_PORT", "8090"))
TONCENTER_INFO_URL = os.getenv("TONCENTER_INFO_URL", "https://toncenter.com/api/v2/getMasterchainInfo")


class DailyZipLogger:
    def __init__(self, log_path: Path, archive_dir: Path) -> None:
        self.log_path = log_path
        self.archive_dir = archive_dir
        self.lock = threading.Lock()
        self.current_day = datetime.now(timezone.utc).date().isoformat()
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        # Start each launch with an empty log file.
        self.log_path.write_text("", encoding="utf-8")

    def _archive_current_log(self, day: str) -> None:
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            return
        base_name = f"cycle_run_{day}.zip"
        zip_path = self.archive_dir / base_name
        if zip_path.exists():
            ts = datetime.now(timezone.utc).strftime("%H%M%S")
            zip_path = self.archive_dir / f"cycle_run_{day}_{ts}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(self.log_path, arcname="cycle_run.log")

    def _rotate_if_needed(self, now_dt: datetime) -> None:
        today = now_dt.date().isoformat()
        if today == self.current_day:
            return
        self._archive_current_log(self.current_day)
        self.log_path.write_text("", encoding="utf-8")
        self.current_day = today

    def log(self, level: str, message: str) -> None:
        now = datetime.now(timezone.utc)
        with self.lock:
            self._rotate_if_needed(now)
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] [{level}] {message}\n"
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line)


LOGGER = DailyZipLogger(RUN_LOG_PATH, REPORTS_DIR)


def log_info(message: str) -> None:
    LOGGER.log("INFO", message)


def log_error(message: str) -> None:
    LOGGER.log("ERROR", message)


@dataclass
class RunRecord:
    run_id: str
    range_from: int
    range_to: int
    started_at: str
    finished_at: Optional[str]
    status: str
    attempts: int
    has_errors: bool
    archive_name: Optional[str]
    duration_sec: Optional[int]
    note: Optional[str]


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.current: Dict[str, object] = {
            "status": "idle",
            "message": "not started",
            "from_seqno": None,
            "to_seqno": None,
            "attempt": 0,
            "started_at": None,
        }
        self.history: List[RunRecord] = []

    def set_current(self, **kwargs: object) -> None:
        with self.lock:
            self.current.update(kwargs)

    def add_record(self, record: RunRecord) -> None:
        with self.lock:
            self.history.insert(0, record)
            _save_state(self.history)

    def snapshot(self) -> Tuple[Dict[str, object], List[RunRecord]]:
        with self.lock:
            return dict(self.current), list(self.history)

    def health_snapshot(self) -> Dict[str, object]:
        with self.lock:
            worker_alive = bool(self.worker_thread and self.worker_thread.is_alive())
            return {
                "worker_alive": worker_alive,
                "stop_requested": self.stop_event.is_set(),
                "current_status": self.current.get("status"),
                "history_size": len(self.history),
                "last_run_id": self.history[0].run_id if self.history else None,
                "last_run_status": self.history[0].status if self.history else None,
                "checked_at": _iso_now(),
            }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_state() -> List[RunRecord]:
    if not STATE_PATH.exists():
        return []
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        out: List[RunRecord] = []
        for item in raw:
            out.append(RunRecord(**item))
        return out
    except Exception:
        return []


def _save_state(items: List[RunRecord]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(i) for i in items], ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _get_seqno() -> int:
    req = urllib.request.Request(TONCENTER_INFO_URL, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return int(payload["result"]["last"]["seqno"])


def _read_env_values() -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):]
        if "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def _update_env_seqnos(to_seqno: int, from_seqno: int) -> None:
    lines: List[str] = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            key = line.strip()
            if key.startswith("export "):
                key = key[len("export "):]
            if key.startswith("TO_SEQNO=") or key.startswith("FROM_SEQNO="):
                continue
            lines.append(line)
    lines.append(f"export TO_SEQNO={to_seqno}")
    lines.append(f"export FROM_SEQNO={from_seqno}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _debug_dumps_non_empty() -> bool:
    if not DEBUG_DUMPS_DIR.exists() or not DEBUG_DUMPS_DIR.is_dir():
        return False
    for _ in DEBUG_DUMPS_DIR.iterdir():
        return True
    return False


def _has_error_artifacts() -> bool:
    checks = [
        ROOT / "failed_txs_pretty.json",
        ROOT / "failed_txs.json",
        ROOT / "failed_traces.json",
    ]
    for item in checks:
        if item.exists() and item.stat().st_size > 0:
            return True
    return _debug_dumps_non_empty()


def _build_archive_name(range_from: int, range_to: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"report_{range_from}_{range_to}_{ts}.zip"


def _archive_results(range_from: int, range_to: int) -> Optional[str]:
    if not _has_error_artifacts():
        return None

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = _build_archive_name(range_from, range_to)
    archive_path = REPORTS_DIR / archive_name

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in [
            "failed_txs_pretty.json",
            "failed_txs.json",
            "failed_traces.json",
            "emulation_report.html",
            "tonemuso_run.log",
        ]:
            p = ROOT / rel
            if p.exists() and p.is_file():
                zf.write(p, arcname=rel)

        if DEBUG_DUMPS_DIR.exists() and DEBUG_DUMPS_DIR.is_dir():
            for path in DEBUG_DUMPS_DIR.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(ROOT)))

    return archive_name


def _run_once(timeout_sec: int) -> Tuple[int, str]:
    proc = subprocess.Popen(
        ["./run.sh"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = []
    start = time.time()
    try:
        while True:
            if proc.stdout is None:
                break
            line = proc.stdout.readline()
            if line:
                output.append(line)
            if proc.poll() is not None:
                break
            if time.time() - start > timeout_sec:
                raise subprocess.TimeoutExpired(cmd="./run.sh", timeout=timeout_sec)
        if proc.stdout is not None:
            tail = proc.stdout.read()
            if tail:
                output.append(tail)
        return int(proc.returncode or 0), "".join(output)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        return 124, "".join(output)


def _next_range(current_from: int, current_to: int) -> Tuple[int, int]:
    return current_to, current_to + DEFAULT_RANGE_SIZE


def _wait_for_next_window(target_to: int, stop_event: threading.Event) -> bool:
    while not stop_event.is_set():
        try:
            new_seqno = _get_seqno()
        except Exception:
            time.sleep(10)
            continue
        if new_seqno >= target_to + DEFAULT_RANGE_SIZE:
            return True
        time.sleep(DEFAULT_POLL_SEC)
    return False


def _worker_loop(state: State) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    env_vals = _read_env_values()
    to_seqno = int(env_vals.get("TO_SEQNO") or 0)
    from_seqno = int(env_vals.get("FROM_SEQNO") or 0)

    if to_seqno <= 0:
        to_seqno = _get_seqno()
    if from_seqno <= 0:
        from_seqno = to_seqno - DEFAULT_RANGE_SIZE
    _update_env_seqnos(to_seqno=to_seqno, from_seqno=from_seqno)
    log_info(f"Worker started with initial range {from_seqno}-{to_seqno}")

    while not state.stop_event.is_set():
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        started = _iso_now()

        state.set_current(
            status="running",
            message="test in progress",
            from_seqno=from_seqno,
            to_seqno=to_seqno,
            attempt=0,
            started_at=started,
        )

        attempt = 0
        exit_code = 0
        note = None
        run_start = time.time()
        while attempt <= DEFAULT_MAX_RESTARTS and not state.stop_event.is_set():
            attempt += 1
            state.set_current(attempt=attempt, message=f"running attempt {attempt}")
            log_info(f"Run {run_id}: start attempt {attempt} for range {from_seqno}-{to_seqno}")
            exit_code, _ = _run_once(timeout_sec=DEFAULT_RUN_TIMEOUT_SEC)
            if exit_code == 0:
                log_info(f"Run {run_id}: attempt {attempt} finished successfully")
                break
            note = f"run.sh exited with code {exit_code}"
            state.set_current(status="restarting", message=note)
            log_error(f"Run {run_id}: {note}")
            if attempt <= DEFAULT_MAX_RESTARTS:
                time.sleep(DEFAULT_RESTART_DELAY_SEC)

        has_errors = _has_error_artifacts()
        archive_name = _archive_results(from_seqno, to_seqno) if has_errors else None
        duration_sec = int(time.time() - run_start)

        if exit_code == 0:
            status = "success_with_errors" if has_errors else "success_clean"
            if has_errors:
                note = note or "errors found, archive created"
            else:
                note = "no errors in range"
        else:
            status = "failed"
            if note is None:
                note = f"failed with code {exit_code}"

        record = RunRecord(
            run_id=run_id,
            range_from=from_seqno,
            range_to=to_seqno,
            started_at=started,
            finished_at=_iso_now(),
            status=status,
            attempts=attempt,
            has_errors=has_errors,
            archive_name=archive_name,
            duration_sec=duration_sec,
            note=note,
        )
        state.add_record(record)
        log_info(
            f"Run {run_id}: finished status={status}, attempts={attempt}, "
            f"errors={has_errors}, archive={archive_name or '-'}"
        )

        state.set_current(
            status="waiting",
            message="waiting for next range",
            from_seqno=from_seqno,
            to_seqno=to_seqno,
            attempt=0,
            started_at=None,
        )

        if not _wait_for_next_window(to_seqno, state.stop_event):
            log_info("Worker stop requested while waiting for next range")
            break

        from_seqno, to_seqno = _next_range(from_seqno, to_seqno)
        _update_env_seqnos(to_seqno=to_seqno, from_seqno=from_seqno)
        log_info(f"Switched to next range {from_seqno}-{to_seqno}")

    state.set_current(status="stopped", message="worker stopped")
    log_info("Worker stopped")


def _parse_bool_filter(v: Optional[str]) -> Optional[bool]:
    if v is None or v == "":
        return None
    raw = v.strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return None


def _filter_history(history: List[RunRecord], query: str, status_filter: str,
                    has_errors_filter: Optional[bool], limit: int) -> List[RunRecord]:
    q = query.strip().lower()
    sf = status_filter.strip().lower()
    out: List[RunRecord] = []
    for r in history:
        if sf and sf != "all" and r.status.lower() != sf:
            continue
        if has_errors_filter is not None and bool(r.has_errors) != has_errors_filter:
            continue
        if q:
            hay = " ".join([
                r.run_id,
                f"{r.range_from}-{r.range_to}",
                r.status,
                r.note or "",
                r.archive_name or "",
            ]).lower()
            if q not in hay:
                continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _html_page(current: Dict[str, object], history: List[RunRecord], query: str,
               status_filter: str, has_errors_filter: Optional[bool]) -> str:
    rows = []
    for r in history:
        download = "-"
        if r.archive_name:
            dl = quote(r.archive_name)
            download = f'<a href="/download/{dl}">{r.archive_name}</a>'
        elif not r.has_errors:
            download = "no errors"

        rows.append(
            "<tr>"
            f"<td>{r.run_id}</td>"
            f"<td>{r.range_from}-{r.range_to}</td>"
            f"<td>{r.status}</td>"
            f"<td>{r.attempts}</td>"
            f"<td>{r.duration_sec or '-'}s</td>"
            f"<td>{r.note or '-'}</td>"
            f"<td>{download}</td>"
            "</tr>"
        )

    rows_html = "\n".join(rows) if rows else "<tr><td colspan='7'>No runs yet</td></tr>"
    from_to = "-"
    if current.get("from_seqno") is not None and current.get("to_seqno") is not None:
        from_to = f"{current['from_seqno']}-{current['to_seqno']}"

    selected_has_errors = "all"
    if has_errors_filter is True:
        selected_has_errors = "yes"
    elif has_errors_filter is False:
        selected_has_errors = "no"

    query_esc = html.escape(query)
    status_esc = html.escape(status_filter or "all")

    return f"""<!doctype html>
<html>
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>TonTVMReplay Cycle</title>
<style>
:root {{
  --bg: #f5f1e8;
  --panel: #fffdf8;
  --ink: #2c2a28;
  --accent: #bb4d00;
  --muted: #746e68;
  --line: #e6dbc9;
}}
body {{
  margin: 0;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  background: radial-gradient(circle at 10% 10%, #fff4dc, var(--bg) 45%), linear-gradient(130deg, #f7ead2, #f4f2ed);
  color: var(--ink);
}}
.container {{
  max-width: 1100px;
  margin: 24px auto;
  padding: 20px;
}}
.card {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 6px 20px rgba(63, 42, 12, 0.08);
}}
h1 {{
  margin: 0 0 12px;
  font-size: 28px;
  letter-spacing: 0.2px;
}}
.status {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}}
.kv {{
  background: #fff7eb;
  border: 1px solid #efddc2;
  border-radius: 10px;
  padding: 10px;
}}
.kv b {{ color: var(--accent); }}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}}
th, td {{
  border-bottom: 1px solid var(--line);
  padding: 8px;
  text-align: left;
  vertical-align: top;
}}
th {{
  background: #fff8ea;
  color: #5d4b2f;
}}
a {{
  color: #a83f00;
  text-decoration: none;
}}
a:hover {{ text-decoration: underline; }}
.muted {{ color: var(--muted); }}
.filters {{
    display: grid;
    grid-template-columns: 1.2fr 1fr 1fr auto;
    gap: 10px;
    margin: 12px 0 14px;
}}
.filters input, .filters select {{
    width: 100%;
    border: 1px solid #dcc9ab;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 14px;
    background: #fffdf9;
    box-sizing: border-box;
}}
.filters button {{
    border: 0;
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 700;
    background: #c14c00;
    color: #fff;
    cursor: pointer;
}}
@media (max-width: 860px) {{
    .filters {{
        grid-template-columns: 1fr;
    }}
}}
</style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>TonTVMReplay Cycle Web</h1>
      <div class="status">
        <div class="kv"><b>Status:</b> {current.get('status')}</div>
        <div class="kv"><b>Message:</b> {current.get('message')}</div>
        <div class="kv"><b>Range:</b> {from_to}</div>
        <div class="kv"><b>Attempt:</b> {current.get('attempt')}</div>
      </div>
      <div class="muted">Auto refresh every 15s</div>
    </div>
    <div style="height: 14px"></div>
    <div class="card">
      <h2>Reports</h2>
            <form class="filters" method="get" action="/">
                <input name="q" type="text" placeholder="Search by run/range/note/archive" value="{query_esc}" />
                <select name="status">
                    <option value="all" {"selected" if status_esc == "all" else ""}>All statuses</option>
                    <option value="success_clean" {"selected" if status_esc == "success_clean" else ""}>success_clean</option>
                    <option value="success_with_errors" {"selected" if status_esc == "success_with_errors" else ""}>success_with_errors</option>
                    <option value="failed" {"selected" if status_esc == "failed" else ""}>failed</option>
                </select>
                <select name="has_errors">
                    <option value="all" {"selected" if selected_has_errors == "all" else ""}>Errors: any</option>
                    <option value="yes" {"selected" if selected_has_errors == "yes" else ""}>Errors: yes</option>
                    <option value="no" {"selected" if selected_has_errors == "no" else ""}>Errors: no</option>
                </select>
                <button type="submit">Apply</button>
            </form>
      <table>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Range</th>
            <th>Status</th>
            <th>Attempts</th>
            <th>Duration</th>
            <th>Note</th>
            <th>Archive</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>
  </div>
<script>
setTimeout(function () {{ location.reload(); }}, 15000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    state: State = None  # type: ignore

    def _send_json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        current, history = self.state.snapshot()
        parsed = urlsplit(self.path)
        route_path = parsed.path
        query = parse_qs(parsed.query)

        if route_path == "/" or route_path == "/index.html":
            q = (query.get("q", [""])[0] or "").strip()
            status_filter = (query.get("status", ["all"])[0] or "all").strip()
            has_errors_filter = _parse_bool_filter((query.get("has_errors", [""])[0] or "").strip())
            try:
                limit = int(query.get("limit", ["200"])[0])
            except Exception:
                limit = 200
            limit = max(1, min(limit, 1000))

            filtered = _filter_history(
                history=history,
                query=q,
                status_filter=status_filter,
                has_errors_filter=has_errors_filter,
                limit=limit,
            )
            page = _html_page(current, filtered, q, status_filter, has_errors_filter).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return

        if route_path == "/api/state":
            q = (query.get("q", [""])[0] or "").strip()
            status_filter = (query.get("status", ["all"])[0] or "all").strip()
            has_errors_filter = _parse_bool_filter((query.get("has_errors", [""])[0] or "").strip())
            try:
                limit = int(query.get("limit", ["200"])[0])
            except Exception:
                limit = 200
            limit = max(1, min(limit, 1000))
            filtered = _filter_history(
                history=history,
                query=q,
                status_filter=status_filter,
                has_errors_filter=has_errors_filter,
                limit=limit,
            )
            self._send_json({
                "current": current,
                "history": [asdict(r) for r in filtered],
            })
            return

        if route_path in ("/health", "/healthz"):
            info = self.state.health_snapshot()
            ok = bool(info["worker_alive"]) and not bool(info["stop_requested"])
            code = HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE
            self._send_json({
                "ok": ok,
                "service": "cycle-web-server",
                "host": DEFAULT_HOST,
                "port": DEFAULT_PORT,
                "details": info,
            })
            return

        if route_path.startswith("/download/"):
            name = unquote(route_path[len("/download/"):]).strip()
            safe_name = os.path.basename(name)
            if safe_name != name:
                self.send_error(HTTPStatus.BAD_REQUEST, "invalid filename")
                return
            file_path = REPORTS_DIR / safe_name
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "file not found")
                return
            data = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename={safe_name}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def log_message(self, fmt: str, *args: object) -> None:
        log_info("web: " + (fmt % args))


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    state = State()
    state.history = _load_state()
    Handler.state = state

    worker = threading.Thread(target=_worker_loop, args=(state,), daemon=True)
    state.worker_thread = worker
    worker.start()

    server = ThreadingHTTPServer((DEFAULT_HOST, DEFAULT_PORT), Handler)

    def _shutdown(_signum: int, _frame) -> None:
        state.stop_event.set()
        try:
            server.shutdown()
        except Exception:
            pass

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log_info(f"Cycle web server started at http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_info("KeyboardInterrupt received")
        pass
    except Exception:
        log_error(f"Fatal server error: {traceback.format_exc()}")
        return 1
    finally:
        state.stop_event.set()
        if state.worker_thread is not None:
            state.worker_thread.join(timeout=10)
        server.server_close()
        log_info("Cycle web server stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
