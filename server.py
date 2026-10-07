#!/usr/bin/env python3
"""Benchboop feedback inbox: testers' playtest zips land here with one button press (Slop P16, 2026-10-07).

Stdlib only. Runs on Railway with a volume at DATA_DIR (default /data).

  POST /upload           body = the zip bytes; headers X-Token (UPLOAD_TOKEN), X-Game, X-Alias, X-Build
                         -> 201 {"name": "<game>/<utc stamp>_<alias>_<build>.zip", "bytes": N}
  GET  /list?token=READ  -> {"files": [{"name", "bytes", "mtime"}]}   (READ_TOKEN only)
  GET  /file/<name>?token=READ -> the zip                                (READ_TOKEN only)
  GET  /health           -> ok

Limits: MAX_MB a file (default 60), UPLOADS_PER_HOUR an IP (default 20). Names are sanitised to
[A-Za-z0-9._-]; nothing is ever overwritten (a stamp to the second plus a counter). Tokens are
compared in constant time. Never logs a token.
"""
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_TOKEN = os.environ.get("UPLOAD_TOKEN", "")
READ_TOKEN = os.environ.get("READ_TOKEN", "")
MAX_BYTES = int(float(os.environ.get("MAX_MB", "60")) * 1024 * 1024)
UPLOADS_PER_HOUR = int(os.environ.get("UPLOADS_PER_HOUR", "20"))
PORT = int(os.environ.get("PORT", "8080"))
SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_recent: dict[str, list[float]] = {}


def clean(s: str, fallback: str, limit: int = 40) -> str:
    s = SAFE.sub("-", (s or "").strip())[:limit].strip("-.")
    return s or fallback


def token_ok(given: str, wanted: str) -> bool:
    return bool(wanted) and hmac.compare_digest(given or "", wanted)


def allowed(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _recent.get(ip, []) if now - t < 3600]
    if len(hits) >= UPLOADS_PER_HOUR:
        _recent[ip] = hits
        return False
    hits.append(now)
    _recent[ip] = hits
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "feedback-inbox/1"

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):  # no tokens in logs: only method, path, code
        path = urlparse(self.path).path
        print(f"{self.client_address[0]} {self.command} {path} {args[1] if len(args) > 1 else ''}", flush=True)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/health":
            return self._json(200, {"ok": True})
        token = parse_qs(url.query).get("token", [""])[0]
        if not token_ok(token, READ_TOKEN):
            return self._json(401, {"error": "no"})
        if url.path == "/list":
            files = []
            for p in sorted(DATA_DIR.rglob("*.zip")):
                st = p.stat()
                files.append({"name": str(p.relative_to(DATA_DIR)), "bytes": st.st_size, "mtime": int(st.st_mtime)})
            return self._json(200, {"files": files})
        if url.path.startswith("/file/"):
            rel = url.path[len("/file/"):]
            target = (DATA_DIR / rel).resolve()
            if not str(target).startswith(str(DATA_DIR.resolve())) or not target.is_file():
                return self._json(404, {"error": "no such file"})
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json(404, {"error": "no such path"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/upload":
            return self._json(404, {"error": "no such path"})
        if not token_ok(self.headers.get("X-Token", ""), UPLOAD_TOKEN):
            return self._json(401, {"error": "no"})
        if not allowed(self.client_address[0]):
            return self._json(429, {"error": "too many uploads this hour"})
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_BYTES:
            return self._json(413, {"error": f"size must be 1..{MAX_BYTES} bytes"})
        data = self.rfile.read(length)
        if data[:2] != b"PK":
            return self._json(400, {"error": "not a zip"})
        game = clean(self.headers.get("X-Game", ""), "game")
        alias = clean(self.headers.get("X-Alias", ""), "anon")
        build = clean(self.headers.get("X-Build", ""), "dev", 16)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        folder = DATA_DIR / game
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{stamp}_{alias}_{build}.zip"
        n = 1
        while (folder / name).exists():
            n += 1
            name = f"{stamp}_{alias}_{build}-{n}.zip"
        (folder / name).write_bytes(data)
        return self._json(201, {"name": f"{game}/{name}", "bytes": len(data)})


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not UPLOAD_TOKEN or not READ_TOKEN:
        print("feedback-inbox: UPLOAD_TOKEN and READ_TOKEN must be set", flush=True)
        raise SystemExit(2)
    print(f"feedback-inbox on :{PORT}, data at {DATA_DIR}, cap {MAX_BYTES} bytes", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
