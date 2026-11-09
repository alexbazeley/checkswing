"""Tests for scripts/resumable_download.sh — the il-backfill.yml ISBE download.

A local HTTP server stands in for ISBE: it honours Range / If-Range, can drop the
connection after N bytes (ISBE does this every few minutes), can republish the
file mid-download, and can answer HEAD without Content-Length (what ISBE's
Cloudflare front does). The property under test is the one that matters for
provenance: the result is always a byte-exact copy of ONE published version,
never a splice of two."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "resumable_download.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("bash") is None,
    reason="needs curl + bash",
)

V1 = bytes(range(256)) * 400            # 102,400 bytes
V2 = bytes(reversed(range(256))) * 410  # different size + content
LM1 = formatdate(1_700_000_000, usegmt=True)
LM2 = formatdate(1_700_086_400, usegmt=True)


class FakeISBE:
    def __init__(self):
        self.body, self.lm = V1, LM1
        self.drop_after: int | None = None   # bytes per GET before hanging up
        self.honour_range = True
        self.republish_after_gets: int | None = None
        self.range_starts: list[int] = []
        self.full_gets = 0
        self._lock = threading.Lock()
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # quiet
                pass

            def do_HEAD(self):  # like ISBE via Cloudflare: no Content-Length
                self.send_response(200)
                self.send_header("Last-Modified", srv.lm)
                self.send_header("Connection", "close")
                self.end_headers()

            def do_GET(self):
                with srv._lock:
                    body, lm = srv.body, srv.lm
                rng = self.headers.get("Range")
                if_range = self.headers.get("If-Range")
                if rng and srv.honour_range and (if_range is None or if_range == lm):
                    a, _, b = rng.removeprefix("bytes=").partition("-")
                    start = int(a)
                    end = int(b) if b else len(body) - 1
                    if start >= len(body):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(body)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    part = body[start:end + 1]
                    if end > start:  # not the 1-byte probe
                        srv.range_starts.append(start)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                else:
                    part = body
                    srv.full_gets += 1
                    self.send_response(200)
                self.send_header("Last-Modified", lm)
                self.send_header("Content-Length", str(len(part)))
                self.send_header("Connection", "close")
                self.end_headers()
                is_probe = rng == "bytes=0-0"
                n = len(part) if (srv.drop_after is None or is_probe) else min(srv.drop_after, len(part))
                try:
                    self.wfile.write(part[:n])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                if not is_probe:
                    with srv._lock:
                        srv._gets = getattr(srv, "_gets", 0) + 1
                        if srv.republish_after_gets and srv._gets >= srv.republish_after_gets:
                            srv.body, srv.lm = V2, LM2
                            srv.republish_after_gets = None

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/Receipts.txt"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def isbe():
    s = FakeISBE()
    yield s
    s.close()


def run(url: str, dest: Path, deadline_in: int = 60) -> subprocess.CompletedProcess:
    env = dict(os.environ, CURL_EXTRA="--noproxy *", RESUME_SLEEP="0")
    return subprocess.run(
        ["bash", str(SCRIPT), url, str(dest), str(int(time.time()) + deadline_in)],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_fresh_download_through_dropped_connections(isbe, tmp_path):
    isbe.drop_after = 30_000  # hang up mid-response, like ISBE
    dest = tmp_path / "Receipts.txt"
    r = run(isbe.url, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    assert dest.read_bytes() == V1
    assert (tmp_path / "Receipts.txt.version").read_text().strip() == f"{len(V1)}|{LM1}"
    assert len(isbe.range_starts) >= 3  # it really resumed


def test_does_not_depend_on_head_content_length(isbe, tmp_path):
    # FakeISBE's HEAD never sends Content-Length (the 2026-11-09 observation).
    r = run(isbe.url, tmp_path / "Receipts.txt")
    assert r.returncode == 0, r.stdout + r.stderr


def test_resumes_a_partial_of_the_same_version(isbe, tmp_path):
    dest = tmp_path / "Receipts.txt"
    dest.write_bytes(V1[:40_000])
    (tmp_path / "Receipts.txt.version").write_text(f"{len(V1)}|{LM1}\n")
    r = run(isbe.url, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    assert dest.read_bytes() == V1
    assert isbe.range_starts[0] == 40_000 and isbe.full_gets == 0


def test_complete_cached_copy_is_reused_without_downloading(isbe, tmp_path):
    dest = tmp_path / "Receipts.txt"
    dest.write_bytes(V1)
    (tmp_path / "Receipts.txt.version").write_text(f"{len(V1)}|{LM1}\n")
    r = run(isbe.url, dest)
    assert r.returncode == 0 and "complete" in r.stdout
    assert isbe.range_starts == [] and isbe.full_gets == 0


@pytest.mark.parametrize("recorded", [f"{len(V1)}|{LM2}", None])
def test_partial_from_another_or_unknown_version_is_discarded_not_spliced(isbe, tmp_path, recorded):
    dest = tmp_path / "Receipts.txt"
    dest.write_bytes(V2[:40_000])  # bytes from a different export
    if recorded:
        (tmp_path / "Receipts.txt.version").write_text(recorded + "\n")
    r = run(isbe.url, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "discarding the partial" in r.stdout
    assert dest.read_bytes() == V1


def test_republish_mid_download_restarts_on_the_new_version(isbe, tmp_path):
    isbe.drop_after = 30_000
    isbe.republish_after_gets = 2  # ISBE swaps the export after two chunks
    dest = tmp_path / "Receipts.txt"
    r = run(isbe.url, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "republished while downloading" in r.stdout
    assert dest.read_bytes() == V2  # wholly the new export — no V1 prefix
    assert (tmp_path / "Receipts.txt.version").read_text().strip() == f"{len(V2)}|{LM2}"


def test_origin_without_range_support_fails_loudly(isbe, tmp_path):
    isbe.honour_range = False
    r = run(isbe.url, tmp_path / "Receipts.txt")
    assert r.returncode == 1 and "could not read size" in r.stdout


def test_deadline_keeps_the_partial_and_its_version(isbe, tmp_path):
    isbe.drop_after = 30_000
    dest = tmp_path / "Receipts.txt"
    dest.write_bytes(V1[:10_000])
    (tmp_path / "Receipts.txt.version").write_text(f"{len(V1)}|{LM1}\n")
    r = run(isbe.url, dest, deadline_in=-1)
    assert r.returncode == 1 and "Deadline reached" in r.stdout
    assert dest.read_bytes() == V1[:10_000]
    assert (tmp_path / "Receipts.txt.version").exists()
