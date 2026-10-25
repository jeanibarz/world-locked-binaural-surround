"""filterpack v1 reference-loader tests (M-D1).

The GOLDEN_DIGEST lock is the cross-language contract: the Rust loader (PyO3) must produce this
exact sha256 for the same fixture, which is how "byte-identical reads from both sides" is proven.
If the format or the fixture changes deliberately, regenerate the fixture and update this constant;
an accidental change trips this test.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from binaural_headtracker import filterpack as fp

FIXTURE = Path(__file__).parent / "fixtures" / "filterpack" / "owner_min"
GOLDEN_DIGEST = "39547a025984b7da1d91d58e917a08b4a7b6a3ed77d2ae6707a4d5d57cd33e57"


def test_loads_and_validates():
    pack = fp.load(FIXTURE)
    assert pack.name == "owner-min-fixture"
    assert pack.samplerate == 48000 and pack.ir_length == 64 and pack.comp == "ctf"
    assert [s.channel for s in pack.speakers] == ["L", "R", "C", "LFE", "RL", "RR"]
    assert pack.lfe_channel == "LFE" and pack.lfe_mode == "brir"
    # non-directional LFE carries no azimuth; the directional speakers do
    lfe = next(s for s in pack.speakers if s.channel == "LFE")
    assert lfe.azimuth_deg is None
    assert next(s for s in pack.speakers if s.channel == "L").azimuth_deg == 30.0
    for s in pack.speakers:
        assert s.left.shape == (64,) and s.right.shape == (64,)
        assert s.left.dtype == np.float64


def test_canonical_digest_is_stable():
    assert fp.canonical_digest(fp.load(FIXTURE)) == GOLDEN_DIGEST


def test_rejects_tamper(tmp_path):
    d = tmp_path / "pack"
    shutil.copytree(FIXTURE, d)
    (d / "L.wav").write_bytes((d / "L.wav").read_bytes()[:-2] + b"\x00\x00")
    with pytest.raises(fp.FilterPackError, match="sha256 mismatch"):
        fp.load(d)


def test_rejects_missing_convention(tmp_path):
    import json
    d = tmp_path / "pack"
    shutil.copytree(FIXTURE, d)
    m = json.loads((d / "manifest.json").read_text())
    del m["convention"]["azimuth_zero"]
    (d / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(fp.FilterPackError, match="convention missing"):
        fp.load(d)


def test_rejects_wrong_version(tmp_path):
    import json
    d = tmp_path / "pack"
    shutil.copytree(FIXTURE, d)
    m = json.loads((d / "manifest.json").read_text())
    m["filterpack_version"] = 999
    (d / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(fp.FilterPackError, match="unsupported filterpack_version"):
        fp.load(d)


def test_rust_loader_byte_identical():
    """M-D1 gate: the Rust loader (PyO3) reads the pack byte-identically to Python.

    Skipped when the native `filterpack_rs` module is not built (same pattern as the kernel's
    optional accelerator); build it with `maturin develop -m rust-filterpack/Cargo.toml`.
    """
    filterpack_rs = pytest.importorskip("filterpack_rs")
    d_py = fp.canonical_digest(fp.load(FIXTURE))
    d_rs = filterpack_rs.canonical_digest_of(str(FIXTURE))
    assert d_rs == d_py == GOLDEN_DIGEST  # diff = 0: identical reads from both sides
