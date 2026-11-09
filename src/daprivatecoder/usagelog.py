"""What each session asked of which model, and what it cost: one JSON object a line in
~/.local/state/daprivatecoder/requests.log. Never what was said: no prompt, reply, file or image.

Kinds of line: "session" (a proxy started: its profile and models), "attested" (the enclave's
attestation: hardware, router release, key fingerprint), "request" (model, sealed or not, status,
tokens, cost, seconds) and "vision" (an image described for a model that can't see)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024          # then the log moves to requests.log.1 (one old file is kept)


class UsageLog:
    def __init__(self, path: Path, session: str):
        self.path = path
        self.session = session
        self._lock = threading.Lock()

    def write(self, kind: str, **fields) -> None:
        from .netpolicy import scrubbed
        # an address in an error never with its password (httpx's errors name the URL they were for)
        fields = {k: scrubbed(v) if isinstance(v, str) else v for k, v in fields.items()}
        record = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": self.session, "kind": kind, **fields}
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass                     # a log that can't be written never stops a request


def read(path: Path, limit: int = 0) -> list[dict]:
    """The log's records, oldest first (the last `limit` of them, if given)."""
    out = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out[-limit:] if limit else out
