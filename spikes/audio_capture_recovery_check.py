"""Check real Windows loopback recovery after capture-thread stalls, without opening audio output.

Run from WSL with a Windows audio_probe.exe (or Windows Python with a local executable).
The source must already be playing continuously. The probe uses the production capture backend;
its wall-clock FIFO drain models output pacing and does not measure DAC playback or DSP performance.
Example:
    python3 spikes/audio_capture_recovery_check.py --probe /mnt/c/.../audio_probe.exe
"""

import argparse
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', required=True)
    parser.add_argument('--source', default='VoiceMeeter Input')
    parser.add_argument('--blocks', nargs='+', type=int, default=[256, 512, 1024])
    parser.add_argument('--stalls-ms', nargs='+', type=int, default=[80, 300])
    parser.add_argument('--latencies-ms', nargs='+', type=float, default=[40.0],
                        help='FIFO latency targets; at 48 kHz / block 256 use 16 for 3 blocks, 21.333333 for 4')
    args = parser.parse_args()
    failed = False
    for block in args.blocks:
        for latency in args.latencies_ms:
            for stall in args.stalls_ms:
                print(f'CASE block={block} stall_ms={stall} latency_ms={latency}', flush=True)
                try:
                    result = subprocess.run(
                        [args.probe, '8', args.source, str(block), str(latency), str(stall)],
                        capture_output=True, text=True, errors='replace', timeout=20,
                    )
                except subprocess.TimeoutExpired:
                    print('FAIL probe exceeded its 20-second deadline', flush=True)
                    failed = True
                    continue
                print(result.stdout, end='', flush=True)
                if result.returncode:
                    print(result.stderr, file=sys.stderr)
                    failed = True
                    continue
                rows = [tuple(map(float, m)) for m in re.findall(
                    r'WINDOW t=([\d.]+) rate_hz=([\d.]+) discontinuities=(\d+)', result.stdout)]
                rate_match = re.search(r'TARGET_DEVICE .* sr=(\d+)', result.stdout)
                if not rate_match or not rows:
                    print('FAIL missing capture measurements', flush=True)
                    failed = True
                    continue
                rate = float(rate_match[1])
                baseline = [r for r in rows if r[0] < 2.5]
                # Ignore the forced-loss interval and allow at most two seconds to recover. The
                # FIFO uses the requested latency target; this deadline also allows host scheduling noise.
                recovered = [r for r in rows if r[0] >= 5 + stall / 1000]
                ok = (baseline and all(.95 * rate <= r[1] <= 1.05 * rate for r in baseline)
                      and len(recovered) >= 2
                      and all(.98 * rate <= r[1] <= 1.02 * rate and r[2] == 0 for r in recovered))
                print('PASS recovery' if ok else 'FAIL source inactive or recovery stayed below rate / kept losing packets', flush=True)
                failed |= not ok
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
