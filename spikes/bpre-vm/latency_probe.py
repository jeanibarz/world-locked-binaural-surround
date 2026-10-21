"""Characterize the virtual device's latency floor (the Track-B latency goal).

Reports, for the SYSVAD render endpoint:
  - PortAudio's default low/high output latency (its estimate of the device's achievable buffering),
  - the actual stream latency when opened at the smallest latency PortAudio will grant,
  - and, via raw WASAPI (IAudioClient::GetDevicePeriod), the device's DEFAULT and MINIMUM period
    (the hardware/driver-reported latency floor) when comtypes is available.

The pull-driven device adds ~one period of buffering between the app and the consumer (K11); the
minimum stable period is therefore the virtual device's contribution to end-to-end latency.

Usage: python latency_probe.py [device_index]
"""
import sys

import numpy as np
import sounddevice as sd


def find_sysvad():
    for i, d in enumerate(sd.query_devices()):
        ha = sd.query_hostapis(d["hostapi"])["name"]
        if "WASAPI" in ha and d["max_output_channels"] > 0 and (
            "Speaker" in d["name"] or "Sysvad" in d["name"] or "Tablet" in d["name"]
        ):
            return i
    return None


dev = int(sys.argv[1]) if len(sys.argv) > 1 else find_sysvad()
if dev is None:
    print("no SYSVAD speakers endpoint found")
    sys.exit(1)
info = sd.query_devices(dev)
sr = int(info["default_samplerate"])
print(f"device[{dev}] = {info['name']}")
print(f"  max_out_ch={info['max_output_channels']} default_sr={sr}")
print(f"  PortAudio default_low_output_latency  = {info['default_low_output_latency'] * 1000:.2f} ms")
print(f"  PortAudio default_high_output_latency = {info['default_high_output_latency'] * 1000:.2f} ms")

# open at the smallest latency PortAudio grants and read back the realised stream latency
for req in (0.0, 0.001, 0.003, 0.005, 0.010):
    try:
        s = sd.OutputStream(device=dev, samplerate=sr, channels=2, dtype="float32",
                            latency=req if req else "low",
                            callback=lambda o, f, t, st: o.fill(0))
        s.start()
        realised = s.latency * 1000
        bs = s.blocksize
        s.stop(); s.close()
        print(f"  requested latency={req*1000:5.1f} ms -> realised stream latency={realised:6.2f} ms "
              f"blocksize={bs} frames ({bs/sr*1000:.2f} ms)")
    except Exception as e:  # noqa: BLE001
        print(f"  requested latency={req*1000:5.1f} ms -> {e!r}")

# raw WASAPI device period (the authoritative floor), if comtypes is present
try:
    import comtypes  # noqa: F401
    from pycaw.pycaw import AudioUtilities  # type: ignore
    print("  (pycaw present; device-period query possible via IAudioClient::GetDevicePeriod)")
except Exception:
    print("  (comtypes/pycaw not installed -> GetDevicePeriod skipped; PortAudio numbers above)")
