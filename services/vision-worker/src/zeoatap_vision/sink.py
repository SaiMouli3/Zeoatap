"""CoreSink: deliver observations to core as NDJSON batches.

POST {core}/internal/v1/observations, one Observation per line,
Authorization: Bearer $ZEOATAP_INTERNAL_TOKEN (packages/contracts/API.md).

- emit() never blocks the caller (the analysis thread): it appends to a
  bounded in-memory buffer and, when that is full, drops the OLDEST
  observation and counts it (dropped_total). Fresh observations matter more
  than stale ones; core detects gaps through its own staleness timers.
- A single sender thread batches up to `batch_max` lines or `flush_interval_s`.
- Every request has explicit connect/read timeouts.
- Failures (network error, 5xx, 503 backlog, 429, 401/403) are retried with
  jittered exponential backoff; the batch is kept and retried in order.
- 400 "bad line": core accepted the first n lines (`accepted` in the reply)
  and rejected the rest. The offending line is dropped and counted
  (rejected_total); the remaining lines are retried.
- The token never appears in logs, status or exceptions.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from .config import redact
from .timing import Backoff

log = logging.getLogger(__name__)

OBSERVATIONS_PATH = "/internal/v1/observations"


class CoreSink:
    def __init__(self, base_url: str, token: str, max_buffer: int = 5000, batch_max: int = 200,
                 flush_interval_s: float = 0.2, connect_timeout_s: float = 2.0, read_timeout_s: float = 5.0,
                 backoff: Backoff | None = None, start: bool = True):
        if not token:
            raise ValueError("CoreSink needs ZEOATAP_INTERNAL_TOKEN")
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"bad core URL: {redact(base_url)}")
        self._scheme, self._host, self._port = parts.scheme, parts.hostname, parts.port
        self._path = (parts.path.rstrip("/") or "") + OBSERVATIONS_PATH
        self.url = redact(f"{parts.scheme}://{parts.netloc}{self._path}")
        self._auth = f"Bearer {token}"
        self.max_buffer, self.batch_max, self.flush_interval_s = max_buffer, batch_max, flush_interval_s
        self.connect_timeout_s, self.read_timeout_s = connect_timeout_s, read_timeout_s
        self.backoff = backoff or Backoff(0.25, 10.0)
        self._buf: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._conn: http.client.HTTPConnection | None = None
        # counters
        self.emitted_total = 0
        self.written = 0  # lines core accepted (name kept for VisionWorker.status)
        self.dropped_total = 0  # buffer overflow, oldest first
        self.rejected_total = 0  # lines core refused with 400
        self.batches_total = 0
        self.failures_total = 0
        self.retries_total = 0
        self.last_error = ""
        self.last_success_ts = 0.0
        self._inflight = 0
        self._thread = threading.Thread(target=self._run, name="core-sink", daemon=True)
        if start:
            self._thread.start()

    # ---- producer side (never blocks on the network) --------------------
    def emit(self, obs: dict) -> None:
        line = json.dumps(obs, separators=(",", ":")).encode()
        with self._lock:
            if len(self._buf) >= self.max_buffer:
                self._buf.popleft()
                self.dropped_total += 1
            self._buf.append(line)
            self.emitted_total += 1
            full = len(self._buf) >= self.batch_max
        if full:
            self._wake.set()

    @property
    def buffered(self) -> int:
        with self._lock:
            return len(self._buf) + self._inflight

    def status(self) -> dict:
        return {
            "kind": "core",
            "url": self.url,
            "emitted_total": self.emitted_total,
            "accepted_total": self.written,
            "dropped_total": self.dropped_total,
            "rejected_total": self.rejected_total,
            "batches_total": self.batches_total,
            "failures_total": self.failures_total,
            "retries_total": self.retries_total,
            "buffered": self.buffered,
            "max_buffer": self.max_buffer,
            "last_success_ts": self.last_success_ts,
            "last_error": self.last_error,
        }

    def close(self, timeout_s: float = 3.0) -> None:
        """Try to flush for up to timeout_s, then stop."""
        end = time.monotonic() + timeout_s
        while self.buffered and time.monotonic() < end and self._thread.is_alive():
            self._wake.set()
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(max(0.1, end - time.monotonic()) + self.read_timeout_s)
        self._close_conn()

    # ---- sender ---------------------------------------------------------
    def _take_batch(self) -> list[bytes]:
        with self._lock:
            n = min(self.batch_max, len(self._buf))
            batch = [self._buf.popleft() for _ in range(n)]
            self._inflight = n
            return batch

    def _requeue_front(self, lines: list[bytes]) -> None:
        """Put unsent lines back in front, still respecting the bound (oldest
        are dropped first if new observations filled the buffer meanwhile)."""
        with self._lock:
            self._inflight = 0
            room = self.max_buffer - len(self._buf)
            if room < len(lines):
                drop = len(lines) - max(room, 0)
                self.dropped_total += drop
                lines = lines[drop:]
            self._buf.extendleft(reversed(lines))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.flush_interval_s)
            self._wake.clear()
            while not self._stop.is_set():
                batch = self._take_batch()
                if not batch:
                    break
                remaining = self._send_with_retry(batch)
                if remaining:
                    self._requeue_front(remaining)
                    break
                with self._lock:
                    self._inflight = 0

    def _send_with_retry(self, batch: list[bytes]) -> list[bytes]:
        """Send until delivered (returns []) or stopping (returns the unsent lines)."""
        first = True
        while batch:
            if not first:
                self.retries_total += 1
            first = False
            ok, batch = self._send_once(batch)
            if ok:
                self.backoff.reset()
                continue
            if self._stop.wait(self.backoff.next_delay()):
                return batch
        return []

    def _send_once(self, batch: list[bytes]) -> tuple[bool, list[bytes]]:
        """Returns (progress_made, lines still to send)."""
        body = b"\n".join(batch) + b"\n"
        self.batches_total += 1
        try:
            conn = self._connection()
            conn.request("POST", self._path, body=body, headers={
                "Authorization": self._auth,
                "Content-Type": "application/x-ndjson",
                "Content-Length": str(len(body)),
            })
            resp = conn.getresponse()
            raw = resp.read(64 * 1024)
            status = resp.status
        except (OSError, http.client.HTTPException) as e:
            self._close_conn()
            self._fail(f"{type(e).__name__}: {e}")
            return False, batch
        try:
            reply = json.loads(raw or b"{}")
        except ValueError:
            reply = {}
        if status == 200:
            n = int(reply.get("accepted", len(batch))) if isinstance(reply, dict) else len(batch)
            self.written += min(n, len(batch))
            self.last_success_ts = time.time()
            return True, []
        if status == 400:
            n = reply.get("accepted") if isinstance(reply, dict) else None
            n = n if isinstance(n, int) and 0 <= n < len(batch) else None
            if n is None:  # cannot tell which line was bad: drop the batch rather than loop forever
                self.rejected_total += len(batch)
                self._fail(f"400 from core, batch of {len(batch)} rejected: {str(reply.get('error', ''))[:200]}")
                return True, []
            self.written += n
            self.rejected_total += 1
            self._fail(f"400 from core, line {n} rejected: {str(reply.get('error', ''))[:200]}")
            return True, batch[n + 1:]
        self._fail(f"HTTP {status} from core")
        return False, batch

    def _fail(self, msg: str) -> None:
        self.failures_total += 1
        self.last_error = redact(msg).replace(self._auth, "Bearer ****")
        log.warning("core sink: %s", self.last_error)

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
            self._conn = cls(self._host, self._port, timeout=self.connect_timeout_s)
            self._conn.connect()
            self._conn.sock.settimeout(self.read_timeout_s)
        return self._conn

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
