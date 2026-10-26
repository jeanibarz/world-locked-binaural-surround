"""Wire-protocol conformance: the Rust control-plane serves byte-for-byte what the Python cockpit
server (`apps/cockpit_web.py`) does, freezing the contract against both servers at once.

Runs the Python `WebCockpit` in-process and the Rust `cockpit_server` binary as a subprocess, fed
identical fixtures (the same cockpit asset dir, config, state JSON, and camera frame), then issues
the same requests to both and asserts equivalence for: static serving (status/Content-Type/body),
`GET /api/get_config` (JSON), `POST /api/<name>` (200 `{}`), the 404, the SSE `data:` framing, and
the MJPEG multipart framing.

The Rust binary path comes from `$WLBS_COCKPIT_SERVER` (default: the shared cargo target); the test
skips if it is not built. Needs the repo's Python deps (numpy/scipy/h5py) + `src`/`apps` on the path.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB_DIR = REPO / "apps" / "web_cockpit"
RUST_BIN = Path(os.environ.get("WLBS_COCKPIT_SERVER", "/mnt/d/cargo-target/release/cockpit_server"))

pytestmark = pytest.mark.skipif(not RUST_BIN.exists(), reason=f"build {RUST_BIN} first")

STATE = {"type": "state", "headline_ms": "42.0", "bars": [{"label": "load", "frac": 0.3}], "n": 7}
FRAME = bytes(range(256)) * 4  # 1024 bytes of fake "JPEG" — framing is what we test, not decoding


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(url: str, method: str = "GET", data: bytes | None = None):
    req = urllib.request.Request(url, method=method, data=data)
    # The Rust control-plane now requires a JSON content-type on POST /api/* (CSRF guard: a
    # cross-site text/plain "simple request" is rejected). Send it so both servers accept the body.
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.headers, r.read()  # headers: case-insensitive email.message.Message
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def _read_stream_until(url: str, needle: bytes, maxbytes: int = 65536, timeout: float = 5.0) -> bytes:
    """Open a streaming endpoint and read until `needle` appears (or timeout)."""
    r = urllib.request.urlopen(url, timeout=timeout)
    buf = b""
    r.fp.raw._sock.settimeout(timeout)  # type: ignore[attr-defined]
    deadline = time.time() + timeout
    while needle not in buf and len(buf) < maxbytes and time.time() < deadline:
        try:
            chunk = r.read(256)
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
    r.close()
    return buf


@pytest.fixture(scope="module")
def servers():
    sys.path.insert(0, str(REPO / "src"))
    sys.path.insert(0, str(REPO / "apps"))
    from cockpit_web import WebCockpit

    # Python server, fed the fixtures.
    wc = WebCockpit(port=_free_port(), open_browser=False)
    wc.set_cameras([("Cam A", 0), ("Cam B", 1)])
    wc.set_hrir_sets(["setA", "setB"])
    wc.set_block_display(256)
    wc.set_method_display("timealign")
    wc._frame = FRAME
    wc.setup()
    py_port = wc._port
    # keep broadcasting the fixture state so any SSE client catches it
    stop = threading.Event()

    def _pump():
        while not stop.is_set():
            wc._broadcast(STATE)
            time.sleep(0.05)

    threading.Thread(target=_pump, daemon=True).start()

    # The Rust server must return the SAME config -> seed it from the Python config().
    cfg = wc.config()
    tmp = REPO / "tests" / "_cp_fixture"
    tmp.mkdir(exist_ok=True)
    (tmp / "config.json").write_text(json.dumps(cfg))
    (tmp / "state.json").write_text(json.dumps(STATE))
    (tmp / "frame.bin").write_bytes(FRAME)

    rust_port = _free_port()
    proc = subprocess.Popen(
        [str(RUST_BIN), "--web-dir", str(WEB_DIR), "--port", str(rust_port),
         "--config", str(tmp / "config.json"), "--state", str(tmp / "state.json"),
         "--frame", str(tmp / "frame.bin"), "--state-interval-ms", "50"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # wait for LISTENING
    for _ in range(100):
        line = proc.stdout.readline()
        if line.startswith("LISTENING"):
            rust_port = int(line.split()[1])
            break
    else:
        proc.kill()
        pytest.fail("rust server did not start")

    yield f"http://127.0.0.1:{py_port}", f"http://127.0.0.1:{rust_port}"

    stop.set()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()


def test_get_config_json_matches(servers):
    py, rs = servers
    _, _, pb = _get(f"{py}/api/get_config")
    _, _, rb = _get(f"{rs}/api/get_config")
    assert json.loads(pb) == json.loads(rb)


@pytest.mark.parametrize("asset,ctype", [("/", "text/html"), ("/index.html", "text/html"),
                                         ("/cockpit.js", "application/javascript"),
                                         ("/icon.svg", "image/svg+xml")])
def test_static_matches(servers, asset, ctype):
    py, rs = servers
    ps, ph, pbody = _get(f"{py}{asset}")
    rsx, rh, rbody = _get(f"{rs}{asset}")
    assert ps == rsx == 200
    assert ph["Content-Type"] == rh["Content-Type"] == ctype
    assert pbody == rbody  # byte-for-byte the same file


def test_404_matches(servers):
    py, rs = servers
    ps, ph, pb = _get(f"{py}/does-not-exist")
    rsx, rh, rb = _get(f"{rs}/does-not-exist")
    assert ps == rsx == 404
    assert pb == rb == b"not found"
    assert ph["Content-Type"] == rh["Content-Type"] == "text/plain"


def test_api_post_matches(servers):
    py, rs = servers
    ps, ph, pb = _get(f"{py}/api/set_block", method="POST", data=b"[256]")
    rsx, rh, rb = _get(f"{rs}/api/set_block", method="POST", data=b"[256]")
    assert ps == rsx == 200
    assert ph["Content-Type"] == rh["Content-Type"] == "application/json"
    assert pb == rb == b"{}"


def test_sse_framing_matches(servers):
    py, rs = servers
    pb = _read_stream_until(f"{py}/events", b"\n\n")
    rb = _read_stream_until(f"{rs}/events", b"\n\n")
    # find the first "data: ...\n\n" frame in each and compare
    def first_data(buf: bytes) -> bytes:
        i = buf.find(b"data: ")
        j = buf.find(b"\n\n", i)
        return buf[i:j]

    pd, rd = first_data(pb), first_data(rb)
    assert pd.startswith(b"data: ") and rd.startswith(b"data: ")
    assert json.loads(pd[len(b"data: "):]) == json.loads(rd[len(b"data: "):]) == STATE


def test_mjpeg_framing_matches(servers):
    py, rs = servers
    pb = _read_stream_until(f"{py}/camera", b"\r\n\r\n", maxbytes=4096)
    rb = _read_stream_until(f"{rs}/camera", b"\r\n\r\n", maxbytes=4096)
    header = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(FRAME)).encode() + b"\r\n\r\n"
    assert header in pb and header in rb
