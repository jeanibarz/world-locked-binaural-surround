#!/usr/bin/env python3
"""Generate the #193 HP-EQ parity golden fixture from the Python oracle.

Drives a unit impulse through `binaural_headtracker.output_eq.load_output_eq` built from the
*bundled* baked config (rust-engine/hp_eq/…), capturing each ear's impulse response. The Rust
`hp_eq::parity_vs_python_oracle` test reproduces the same impulse and compares against this file.

Writes `rust-engine/hp_eq/oracle_golden_48k.f32`: left IR then right IR, each N f32 little-endian.

Run from the repo root:  PYTHONPATH=src python3 tools/gen_hp_eq_golden.py
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

from binaural_headtracker.output_eq import load_output_eq

N = 32768
BLOCK = 256
FS = 48000

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "rust-engine" / "hp_eq" / "baked_7p1_rev26_final.txt"
OUT = ROOT / "rust-engine" / "hp_eq" / "oracle_golden_48k.f32"


def main() -> int:
    eq = load_output_eq(CONFIG, samplerate=FS, block_size=BLOCK)
    left = np.zeros(N)
    right = np.zeros(N)
    for blk in range(N // BLOCK):
        x = np.zeros((BLOCK, 2))
        if blk == 0:
            x[0, 0] = 1.0  # impulse into both ears at sample 0
            x[0, 1] = 1.0
        y = eq.process_block(x)
        left[blk * BLOCK:(blk + 1) * BLOCK] = y[:, 0]
        right[blk * BLOCK:(blk + 1) * BLOCK] = y[:, 1]

    data = np.concatenate([left, right]).astype("<f4")
    OUT.write_bytes(data.tobytes())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"  L peak={np.max(np.abs(left)):.4f}  R peak={np.max(np.abs(right)):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
