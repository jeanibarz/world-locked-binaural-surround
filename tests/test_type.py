"""Tests for the host console's TrueType text renderer (apps/_type, B6.8).

Pure numpy + Pillow — no cv2 — so these run on headless CI/WSL. They skip entirely if
Pillow/FreeType is unavailable, since then the console falls back to Hershey and there is
nothing of _type to exercise.
"""

from __future__ import annotations

import _type
import numpy as np
import pytest

pytestmark = pytest.mark.skipif(not _type.available(), reason="Pillow/FreeType not installed")

BG = (23, 16, 13)  # the console background, BGR


def _blank(h=60, w=400):
    img = np.empty((h, w, 3), np.uint8)
    img[:] = BG
    return img


def test_bundled_faces_resolve_to_expected_files():
    import os

    cases = {
        ("mono", "regular"): "IBMPlexMono-Regular.ttf",
        ("mono", "semibold"): "IBMPlexMono-SemiBold.ttf",
        ("display", "semibold"): "Rajdhani-SemiBold.ttf",
        ("display", "bold"): "Rajdhani-Bold.ttf",
        ("sans", "regular"): "IBMPlexSans-var.ttf",
        ("sans", "semibold"): "IBMPlexSans-var.ttf",
    }
    for (fam, wt), fname in cases.items():
        f = _type._font(fam, wt, 20)
        assert f is not None
        assert os.path.basename(getattr(f, "path", "")) == fname, (fam, wt)


def test_text_width_positive_and_monotonic():
    assert _type.text_width("", 20) == 0
    w1 = _type.text_width("HEALTHY", 18, family="mono")
    assert w1 > 0
    # longer string is wider; bigger size is wider
    assert _type.text_width("HEALTHY DIAGNOSTICS", 18, family="mono") > w1
    assert _type.text_width("HEALTHY", 30, family="mono") > w1


def test_mono_is_fixed_advance():
    # IBM Plex Mono: every glyph shares one advance, so N chars == N x one char. Check the raw
    # font metric (text_width rounds each call, which would mask this at ~0.5px/char).
    f = _type._font("mono", "regular", 24)
    a = f.getlength("M")
    assert a > 0
    assert abs(f.getlength("M" * 10) - 10 * a) < 0.5
    # a proportional face is *not* fixed-advance (sanity: the mono test is meaningful)
    g = _type._font("sans", "regular", 24)
    assert abs(g.getlength("ll") - g.getlength("MM")) > 1.0


def test_draw_writes_glyphs_and_spares_background():
    img = _blank()
    assert _type.draw_text(img, "HELLO", 10, 40, (245, 237, 232), 24, family="sans") is True
    changed = (img != np.array(BG, np.uint8)).any(axis=-1)
    assert changed.any(), "no glyph pixels were drawn"
    # the text sits on the left; the far-right margin must be untouched background
    assert not changed[:, 380:].any()


def test_draw_uses_requested_color():
    img = _blank()
    green = (90, 220, 100)  # BGR
    _type.draw_text(img, "####", 10, 40, green, 28, family="display", weight="bold")
    # the greenest drawn pixel should be close to the requested colour (AA softens the rest)
    diff = np.abs(img.astype(int) - np.array(green, int)).sum(axis=-1)
    assert diff.min() < 40


def test_empty_and_clipped_do_not_crash():
    img = _blank()
    assert _type.draw_text(img, "", 10, 40, (255, 255, 255), 20) is False
    # fully off-screen to the left and below — handled, no exception, no error return path
    assert _type.draw_text(img, "edge", -500, 40, (255, 255, 255), 20) is True
    assert _type.draw_text(img, "edge", 10, 5000, (255, 255, 255), 20) is True


def test_unknown_family_falls_back_not_crash():
    img = _blank()
    # an unknown family resolves via the sans system/default path rather than raising
    assert _type.draw_text(img, "x", 10, 40, (255, 255, 255), 20, family="nope") is True
    assert _type.text_width("x", 20, family="nope") >= 0


def test_raster_error_degrades_to_false(monkeypatch):
    # A rasterisation failure must return False (so _console falls back to Hershey), never
    # propagate into the real-time render loop.
    def boom(*_a, **_k):
        raise RuntimeError("raster boom")

    monkeypatch.setattr(_type, "_mask", boom)
    img = _blank()
    assert _type.draw_text(img, "x", 10, 40, (255, 255, 255), 20) is False
    assert not (img != np.array(BG, np.uint8)).any()  # nothing drawn
