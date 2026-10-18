"""Drive ``latency_bench`` across a grid of settings and summarise (B8.5b).

Runs the benchmark once per configuration as a **fresh subprocess** — so each gets a clean
device re-acquire, exactly like restarting the app — waits for it to stabilise (the bench
discards its own warm-up), parses the JSON, and prints a ranked table plus the component
breakdown, so you can see at a glance which knob moves which stage. Writes the raw rows to
``--out`` (JSON) for later inspection.

Headless default (``--output sim``) sweeps block/cushion/method anywhere. On the host, pass
``--output device --audio-device FiiO`` to measure the real device-buffer term too.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent / "latency_bench.py"
ROOT = Path(__file__).resolve().parents[1]


def _run_one(cfg: dict, common: dict) -> dict | None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT / "apps"),
                                         env.get("PYTHONPATH", "")])
    cmd = [sys.executable, str(BENCH), "--json",
           "--brir-dir", common["brir_dir"],
           "--output", common["output"], "--warmup", str(common["warmup"]),
           "--seconds", str(common["seconds"]), "--device-ms", str(common["device_ms"]),
           "--method", cfg["method"], "--block", str(cfg["block"]),
           "--latency-ms", str(cfg["latency_ms"]), "--grid-step", str(cfg["grid_step"])]
    if cfg["rust"]:
        cmd.append("--rust")
    if cfg["exclusive"]:
        cmd.append("--exclusive")
    if common["audio_device"]:
        cmd += ["--audio-device", common["audio_device"]]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=env,
                             timeout=common["warmup"] + common["seconds"] + 60)
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT: {cfg}", file=sys.stderr)
        return None
    line = next((ln for ln in reversed(out.stdout.splitlines()) if ln.startswith("{")), None)
    if line is None:
        print(f"  FAILED: {cfg}\n{out.stderr[-500:]}", file=sys.stderr)
        return None
    return json.loads(line)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="sweep latency_bench over a grid of settings")
    p.add_argument("--brir-dir", required=True)
    p.add_argument("--output", default="sim", choices=("sim", "device"))
    p.add_argument("--audio-device", default=None)
    p.add_argument("--device-ms", type=float, default=0.0, help="nominal device buffer in sim mode")
    p.add_argument("--blocks", type=int, nargs="+", default=[256, 512, 1024])
    p.add_argument("--latencies", type=float, nargs="+", default=[10.0, 20.0, 40.0])
    p.add_argument("--methods", nargs="+", default=["timealign"])
    p.add_argument("--grid-steps", type=float, nargs="+", default=[10.0])
    p.add_argument("--rust", action="store_true", help="benchmark the native kernel path")
    p.add_argument("--exclusive-modes", nargs="+", default=["shared"],
                   choices=("shared", "exclusive"),
                   help="WASAPI output modes to compare (device mode): e.g. shared exclusive")
    p.add_argument("--warmup", type=float, default=3.0)
    p.add_argument("--seconds", type=float, default=8.0)
    p.add_argument("--out", default="latency_sweep_results.json")
    args = p.parse_args(argv)

    common = {"brir_dir": args.brir_dir, "output": args.output, "device_ms": args.device_ms,
              "audio_device": args.audio_device, "warmup": args.warmup, "seconds": args.seconds}
    grid = [{"block": b, "latency_ms": lm, "method": m, "grid_step": gs, "rust": args.rust,
             "exclusive": xm == "exclusive"}
            for m, b, lm, gs, xm in itertools.product(args.methods, args.blocks, args.latencies,
                                                       args.grid_steps, args.exclusive_modes)]
    print(f"sweeping {len(grid)} configs ({args.output} output, "
          f"~{args.warmup + args.seconds:.0f}s each)...\n")
    rows = []
    for i, cfg in enumerate(grid, 1):
        print(f"[{i}/{len(grid)}] block {cfg['block']} lat {cfg['latency_ms']} "
              f"{cfg['method']} ...", flush=True)
        r = _run_one(cfg, common)
        if r:
            rows.append(r)

    Path(args.out).write_text(json.dumps(rows, indent=2))
    rows.sort(key=lambda r: r["total_ms"]["p50"])
    print(f"\n{'block':>5} {'lat':>4} {'excl':>4} {'cush':>4} {'load%':>6} {'drop':>4} "
          f"{'stale':>6} {'queue':>6} {'dev':>5} {'TOTAL p50':>9} {'p95':>5} {'ok':>3}")
    for r in rows:
        print(f"{r['block']:>5} {r['latency_ms']:>4.0f} "
              f"{'excl' if r.get('exclusive') else 'shar':>4} {r['cushion_blocks']:>4} "
              f"{r['render_load'] * 100:>5.0f}% {r['dropouts']:>4} "
              f"{r['stale_ms']['p50']:>6.0f} {r['queue_ms']['p50']:>6.0f} {r['device_ms']:>5.0f} "
              f"{r['total_ms']['p50']:>9.0f} {r['total_ms']['p95']:>5.0f} "
              f"{'Y' if r['integrity']['ok'] else 'N':>3}")
    clean = [r for r in rows if r["integrity"]["ok"]]
    if clean:
        best = clean[0]
        print(f"\nbest dropout-free: block {best['block']} / latency-ms {best['latency_ms']:.0f}"
              f" → total p50 {best['total_ms']['p50']:.0f} ms (p95 {best['total_ms']['p95']:.0f})")
    print(f"\nraw rows → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
