"""B0.3 — Windows virtual-device spike (virtual 7.1 sink + loopback capture).

Runs on the **Windows host**, not on WSL (WSL has no WASAPI). Drive it from the
WSL repo via interop, e.g.::

    WINPATH=$(wslpath -w spikes/b0_3_virtual_device.py)
    py.exe -3.12 "$WINPATH" --seconds 1.0

Needs ``sounddevice`` + ``numpy`` on the Windows interpreter
(``py -3.12 -m pip install sounddevice numpy``; recorded as the ``audio``
optional-extra in ``pyproject.toml``).

What it does, and why:

1. **Enumerate** host endpoints and resolve roles at runtime (indices drift —
   journal 02): find the virtual multichannel **sink** an app would select as its
   7.1 output (FR-2) and the **capture** endpoint in the same virtual-cable
   family that carries its stream back.
2. **Capture the virtual device's stream** (B0.3's core requirement): open a
   short WASAPI input stream on that capture endpoint and confirm frames arrive,
   reporting the captured peak level and the stream's reported input latency.
3. **Complete B0.2's deferred round-trip**: render a short, quiet sine sweep to
   the virtual sink and capture the far end, recovering the delay by
   cross-correlation. ``WasapiSettings`` here exposes no ``loopback`` flag
   (journal 02), so this is a *virtual-cable* loopback: it reports a real
   round-trip only if the cable's render strip is routed to its capture bus (for
   VoiceMeeter VAIO, strip → bus B1). If silent, it says so and points at the
   setup doc rather than reporting a spurious 0 ms.

This never changes the host's mixer and never installs software — it reports the
current routing truth. Prints a human summary and a ``RESULT_JSON:`` line for the
journal.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

# Make the repo's portable helpers importable when run by its file path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import sounddevice as sd  # noqa: E402  (after sys.path shim)

from binaural_headtracker.audio_spike import (  # noqa: E402
    estimate_lag_samples,
    format_device_table,
    log_sweep,
    samples_to_ms,
)
from binaural_headtracker.virtual_device import (  # noqa: E402
    loopback_for_sink,
    pair_loopbacks,
    pick_virtual_sink,
)


def wasapi_hostapi_index() -> int | None:
    for i, h in enumerate(sd.query_hostapis()):
        if "wasapi" in h["name"].lower():
            return i


def wasapi_devices(wasapi: int | None) -> tuple[list[dict], list[int]]:
    """All devices plus the subset of indices that live on the WASAPI host API."""
    devices = list(sd.query_devices())
    on = [i for i, d in enumerate(devices) if wasapi is not None and d["hostapi"] == wasapi]
    return devices, on


def capture_stream(device: int, seconds: float) -> dict:
    """Open the virtual capture endpoint and confirm we can read its stream.

    Proves B0.3's "capture its stream": reports reported input latency, frames
    read, and the peak level (near-zero is still a successful *capture* — it just
    means nothing is currently routed to the cable's bus).
    """
    try:
        d = sd.query_devices(device)
        sr = float(d["default_samplerate"])
        ch = min(2, int(d["max_input_channels"]))
        shared = sd.WasapiSettings(exclusive=False, auto_convert=True)
        frames: list[np.ndarray] = []
        stream = sd.InputStream(
            samplerate=sr, device=device, channels=ch, dtype="float32", extra_settings=shared
        )
        with stream:
            reported_ms = round(1000.0 * float(stream.latency), 3)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                avail = stream.read_available
                if avail:
                    data, _ = stream.read(avail)
                    frames.append(data.copy())
                else:
                    time.sleep(0.005)
        captured = np.concatenate(frames, axis=0) if frames else np.zeros((0, ch), np.float32)
        peak = float(np.max(np.abs(captured))) if captured.size else 0.0
        return {
            "ok": True,
            "device": int(device),
            "name": d["name"],
            "channels": ch,
            "samplerate": sr,
            "reported_latency_ms": reported_ms,
            "frames_read": int(captured.shape[0]),
            "peak": round(peak, 5),
            "carrying_signal": peak > 1e-3,
        }
    except Exception as exc:  # noqa: BLE001 — spike: record, don't crash
        return {"ok": False, "device": int(device), "error": f"{type(exc).__name__}: {exc}"}


def measure_loopback(out_device: int, in_device: int, seconds: float, amplitude: float) -> dict:
    """Render a quiet sweep to ``out_device`` and recover its delay from ``in_device``.

    A real WASAPI round-trip through the virtual cable: output + routing + input
    buffering. Silence means the cable's render strip is not routed to its
    capture bus — a config fact, not a 0 ms result, so it is flagged.
    """
    try:
        sr = float(sd.query_devices(out_device)["default_samplerate"])
        out_ch = min(2, int(sd.query_devices(out_device)["max_output_channels"]))
        in_ch = min(2, int(sd.query_devices(in_device)["max_input_channels"]))
        probe = log_sweep(sr, duration_s=0.05)
        total = int(sr * seconds)
        lead = int(sr * 0.2)  # silent lead so the delay is unambiguous
        signal = np.zeros((total, out_ch), dtype=np.float32)
        for c in range(out_ch):
            signal[lead : lead + probe.size, c] = amplitude * probe

        shared = sd.WasapiSettings(exclusive=False, auto_convert=True)
        frames: list[np.ndarray] = []
        rec = sd.InputStream(
            samplerate=sr, device=in_device, channels=in_ch, dtype="float32", extra_settings=shared
        )
        with rec:
            play = sd.OutputStream(
                samplerate=sr,
                device=out_device,
                channels=out_ch,
                dtype="float32",
                extra_settings=shared,
            )
            with play:
                play.write(signal)
                deadline = time.monotonic() + seconds + 0.3
                while time.monotonic() < deadline:
                    avail = rec.read_available
                    if avail:
                        data, _ = rec.read(avail)
                        frames.append(data.copy())
                    else:
                        time.sleep(0.005)
        if not frames:
            return {"ok": False, "error": "no frames captured"}
        captured = np.concatenate(frames, axis=0)
        mono = captured[:, 0] if captured.ndim > 1 else captured
        peak = float(np.max(np.abs(mono)))
        signal_present = peak > 1e-3
        lag = estimate_lag_samples(probe, mono) if signal_present else 0
        return {
            "ok": signal_present,
            "signal_present": signal_present,
            "samplerate": sr,
            "out_device": int(out_device),
            "in_device": int(in_device),
            "lag_samples": int(lag),
            "lead_samples": int(lead),
            "roundtrip_ms": (
                round(samples_to_ms(max(lag - lead, 0), sr), 3) if signal_present else None
            ),
            "captured_frames": int(mono.size),
            "peak": round(peak, 5),
            "note": None
            if signal_present
            else "no signal captured; cable render->capture routing not configured "
            "(VoiceMeeter: assign the VAIO strip to bus B1 -- see "
            "docs/virtual-device-setup.md)",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _meta(devices: list[dict], i: int | None) -> dict:
    if i is None:
        return {"device": None}
    d = devices[i]
    return {
        "device": i,
        "name": d["name"],
        "max_output_channels": d["max_output_channels"],
        "max_input_channels": d["max_input_channels"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="B0.3 Windows virtual-device spike")
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--amplitude", type=float, default=0.1, help="loopback sweep level (quiet)")
    ap.add_argument("--min-channels", type=int, default=8, help="channels a 7.1 sink must expose")
    ap.add_argument("--no-loopback", action="store_true", help="skip the sweep (enumerate only)")
    args = ap.parse_args()

    # The Windows console defaults to cp1252; force UTF-8 so any device name or
    # note that carries a non-ASCII character cannot abort the run mid-report.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    hostapis = list(sd.query_hostapis())
    wasapi = wasapi_hostapi_index()
    devices, wasapi_idx = wasapi_devices(wasapi)
    allow = set(wasapi_idx)  # resolve roles on WASAPI only (NFR-2; MME truncates names)

    print(f"PortAudio: {sd.get_portaudio_version()[1]}")
    print(f"Platform : {sys.platform}  |  WASAPI host API index: {wasapi}\n")
    print(format_device_table(devices, hostapis))

    sink = pick_virtual_sink(devices, min_output_channels=args.min_channels, allow=allow)
    capture = loopback_for_sink(devices, sink, allow=allow) if sink is not None else None
    pairs = pair_loopbacks(devices, allow=allow)

    def label(i: int | None) -> str:
        return f"#{i} {devices[i]['name']}" if i is not None else "none"

    print(f"\nVirtual 7.1 sink (>= {args.min_channels}-ch): {label(sink)}")
    print(f"Its loopback capture endpoint   : {label(capture)}")
    print("All virtual render->capture pairs:")
    for p in pairs:
        print(
            f"  [{p.family}] render {label(p.render)} ({p.render_channels}ch) "
            f"-> capture {label(p.capture)} ({p.capture_channels}ch)"
        )

    result: dict = {
        "portaudio": sd.get_portaudio_version()[1],
        "wasapi_hostapi_index": wasapi,
        "virtual_sink": _meta(devices, sink),
        "loopback_capture": _meta(devices, capture),
        "pairs": [
            {
                "family": p.family,
                "render": p.render,
                "capture": p.capture,
                "render_channels": p.render_channels,
                "capture_channels": p.capture_channels,
            }
            for p in pairs
        ],
    }

    if sink is None:
        result["status"] = "no_virtual_sink"
        print("\nNo virtual 7.1 sink found — install one (see docs/virtual-device-setup.md).")
    elif capture is None:
        result["status"] = "sink_without_capture"
        result["capture_probe"] = None
        result["loopback_roundtrip"] = {
            "ok": False,
            "error": "virtual sink has no capture half; use an external cable (see doc)",
        }
    else:
        result["capture_probe"] = capture_stream(capture, min(args.seconds, 0.5))
        if args.no_loopback:
            result["loopback_roundtrip"] = {"ok": False, "error": "skipped (--no-loopback)"}
        else:
            result["loopback_roundtrip"] = measure_loopback(
                sink, capture, args.seconds, args.amplitude
            )
        result["status"] = (
            "loopback_measured" if result["loopback_roundtrip"].get("ok") else "loopback_silent"
        )

    print("\n--- capture probe ---")
    print(result.get("capture_probe"))
    print("--- loopback round-trip ---")
    print(result.get("loopback_roundtrip"))
    print("\nRESULT_JSON:" + json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
