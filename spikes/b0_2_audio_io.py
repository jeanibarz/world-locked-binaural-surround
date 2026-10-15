"""B0.2 — Windows audio-I/O spike (real WASAPI).

Runs on the **Windows host**, not on WSL: WSL exposes only WSLg PulseAudio, which
is not representative of the Windows low-latency path. Drive it from the WSL repo
via interop, e.g.::

    WINPATH=$(wslpath -w spikes/b0_2_audio_io.py)
    py.exe -3.12 "$WINPATH" --seconds 1.0

It needs ``sounddevice`` + ``numpy`` on the Windows interpreter
(``py -3.12 -m pip install sounddevice numpy``; the ``audio`` optional-extra in
``pyproject.toml`` records the dependency).

What it does:

1. Enumerate host APIs + devices; highlight the WASAPI endpoints.
2. Report the WASAPI-reported stream latency (the figure the engine will face)
   for a stereo output and a multichannel input, in shared mode (``latency='low'``
   and ``'high'``) and a best-effort exclusive-mode probe.
3. Measure a real WASAPI round-trip through a virtual-audio loopback when one is
   present (play to a "VoiceMeeter Input" render endpoint, capture the matching
   "VoiceMeeter Output" endpoint), cross-correlating a short sine sweep to recover
   the delay in milliseconds. This emits a brief, quiet sweep.

``sounddevice.WasapiSettings`` in this PortAudio build exposes ``exclusive`` and
``auto_convert`` only (no ``loopback`` flag) — hence the virtual-cable loopback
rather than a WASAPI render-loopback capture. Prints a human summary and a
``RESULT_JSON:`` line for the journal.
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


def wasapi_hostapi_index() -> int | None:
    for i, h in enumerate(sd.query_hostapis()):
        if "wasapi" in h["name"].lower():
            return i
    return None


def pick_endpoints(wasapi: int | None) -> dict:
    """Choose WASAPI endpoints to probe: a stereo out, a multichannel in, and a
    virtual-cable loopback pair if one is installed."""
    devices = list(sd.query_devices())
    on_wasapi = [i for i, d in enumerate(devices) if wasapi is not None and d["hostapi"] == wasapi]

    def named(substr, want_out):
        for i in on_wasapi:
            d = devices[i]
            ch = d["max_output_channels"] if want_out else d["max_input_channels"]
            if substr.lower() in d["name"].lower() and ch > 0:
                return i
        return None

    host = sd.query_hostapis(wasapi) if wasapi is not None else {}
    default_out = host.get("default_output_device")
    hw_out = named("casque", want_out=True) or named("fiio", want_out=True)
    multi_in = max(
        on_wasapi,
        key=lambda i: devices[i]["max_input_channels"],
        default=host.get("default_input_device"),
    )
    loop_out = named("voicemeeter input", want_out=True)
    loop_in = named("voicemeeter output", want_out=False)
    return {
        "default_out": default_out,
        "hw_out": hw_out,
        "multi_in": multi_in,
        "loop_out": loop_out,
        "loop_in": loop_in,
        "devices": devices,
    }


def reported_latency(device: int, channels: int, kind: str, latency, settings) -> dict:
    """Open a short-lived stream and report PortAudio's latency, or the error."""
    try:
        common = dict(device=device, channels=channels, dtype="float32", extra_settings=settings)
        stream = (
            sd.OutputStream(latency=latency, **common)
            if kind == "output"
            else sd.InputStream(latency=latency, **common)
        )
        with stream:
            return {
                "ok": True,
                "samplerate": float(stream.samplerate),
                "latency_ms": round(1000.0 * float(stream.latency), 3),
                "blocksize": int(stream.blocksize),
            }
    except Exception as exc:  # noqa: BLE001 — spike: record, don't crash
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def measure_loopback(out_device: int, in_device: int, seconds: float) -> dict:
    """Render a sweep to ``out_device`` and capture it from ``in_device``.

    With a virtual cable (VoiceMeeter), that is a real electrical WASAPI
    round-trip; the recovered delay is output + routing + input buffering.
    """
    try:
        sr = float(sd.query_devices(out_device)["default_samplerate"])
        probe = log_sweep(sr, duration_s=0.05)
        total = int(sr * seconds)
        lead = int(sr * 0.2)  # silence lead so the delay is unambiguous
        signal = np.zeros((total, 2), dtype=np.float32)
        signal[lead : lead + probe.size, 0] = 0.3 * probe
        signal[lead : lead + probe.size, 1] = 0.3 * probe

        in_ch = min(2, sd.query_devices(in_device)["max_input_channels"])
        shared = sd.WasapiSettings(exclusive=False, auto_convert=True)
        frames = []
        rec = sd.InputStream(
            samplerate=sr,
            device=in_device,
            channels=in_ch,
            dtype="float32",
            extra_settings=shared,
        )
        with rec:
            play = sd.OutputStream(
                samplerate=sr,
                device=out_device,
                channels=2,
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
        lag = estimate_lag_samples(probe, mono)
        # A silent capture means the virtual cable's routing did not carry the
        # signal (e.g. VoiceMeeter VAIO not assigned to the B bus) — the delay is
        # then meaningless; flag it rather than report a spurious 0 ms.
        signal_present = peak > 1e-3
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
            "peak": round(peak, 4),
            "note": None
            if signal_present
            else "no signal captured; virtual routing not configured (see B0.3)",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="B0.2 Windows WASAPI audio-I/O spike")
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--no-loopback", action="store_true")
    args = ap.parse_args()

    hostapis = list(sd.query_hostapis())
    wasapi = wasapi_hostapi_index()
    ep = pick_endpoints(wasapi)
    devices = ep["devices"]

    print(f"PortAudio: {sd.get_portaudio_version()[1]}")
    print(f"Platform : {sys.platform}  |  WASAPI host API index: {wasapi}\n")
    print(format_device_table(devices, hostapis))

    def label(i):
        return f"#{i} {devices[i]['name']}" if i is not None else "none"

    do, hw, mi = ep["default_out"], ep["hw_out"], ep["multi_in"]
    print(
        f"\nProbe targets — default out: {label(do)} | hw out: {label(hw)} | "
        f"multi in: {label(mi)} | "
        f"loopback: {label(ep['loop_out'])} -> {label(ep['loop_in'])}"
    )

    shared = sd.WasapiSettings(exclusive=False, auto_convert=True)
    excl = sd.WasapiSettings(exclusive=True)
    mi_ch = min(2, devices[mi]["max_input_channels"]) if mi is not None else 2

    def dev_meta(i):
        if i is None:
            return {"device": None, "name": None}
        d = devices[i]
        return {
            "device": i,
            "name": d["name"],
            "max_output_channels": d["max_output_channels"],
            "max_input_channels": d["max_input_channels"],
        }

    result = {
        "portaudio": sd.get_portaudio_version()[1],
        "wasapi_hostapi_index": wasapi,
        "default_out": dev_meta(do),
        "hw_out": dev_meta(hw),
        "multi_in": dev_meta(mi),
        # The default output is the WASAPI endpoint the engine will actually feed.
        "out_default_shared_low": reported_latency(do, 2, "output", "low", shared),
        "out_default_shared_high": reported_latency(do, 2, "output", "high", shared),
        "out_default_exclusive_low": reported_latency(do, 2, "output", "low", excl),
        # The hardware headphone DAC (may be held by the virtual mixer).
        "out_hw_shared_low": reported_latency(hw, 2, "output", "low", shared),
        "out_hw_exclusive_low": reported_latency(hw, 2, "output", "low", excl),
        "in_shared_low": reported_latency(mi, mi_ch, "input", "low", shared),
    }
    if not args.no_loopback and ep["loop_out"] is not None and ep["loop_in"] is not None:
        result["loopback_roundtrip"] = measure_loopback(
            int(ep["loop_out"]), int(ep["loop_in"]), args.seconds
        )
    else:
        result["loopback_roundtrip"] = {"ok": False, "error": "no virtual-cable pair"}

    print("\n--- reported WASAPI latencies ---")
    for k in (
        "out_default_shared_low",
        "out_default_shared_high",
        "out_default_exclusive_low",
        "out_hw_shared_low",
        "out_hw_exclusive_low",
        "in_shared_low",
    ):
        print(f"{k:26}: {result[k]}")
    print(f"{'loopback':26}: {result['loopback_roundtrip']}")
    print("\nRESULT_JSON:" + json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
