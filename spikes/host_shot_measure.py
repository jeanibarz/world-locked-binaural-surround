"""Measure a host screenshot instead of eyeballing it (run with the host `py.exe -3.12`).

    py.exe -3.12 host_shot_measure.py SHOT.png zoom  X0 Y0 X1 Y1 OUT.png [factor]
        crop + nearest-neighbour upscale a region so glyph/box edges are countable (then Read it).
    py.exe -3.12 host_shot_measure.py SHOT.png comps COLOUR [X0 Y0 X1 Y1] [--axis AX]
        connected components of one overlay colour -> bbox, centre, and (with --axis) the signed
        distance of each centre from a vertical axis, so left/right mirror pairs can be compared.
        COLOUR: red | green | magenta | cyan | white | r,g,b[,tol]

Debug overlays are drawn in pure saturated colours (red box, green tick, magenta axis) that the
UI palette never uses, so they separate cleanly; text ink is `white` (bright, low-saturation).
"""
import sys

import numpy as np
from PIL import Image

NAMED = {
    "red": ((255, 0, 0), 70), "green": ((0, 255, 0), 70), "magenta": ((255, 0, 255), 70),
    "cyan": ((0, 230, 230), 90), "white": ((235, 235, 235), 95),
}


def mask_for(im, colour):
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    if colour == "white":  # bright + unsaturated (anti-aliased text ink)
        return ((r + g + b) / 3 > 100) & (abs(r - g) < 40) & (abs(g - b) < 60)
    if colour in NAMED:
        (cr, cg, cb), tol = NAMED[colour]
    else:
        parts = [int(p) for p in colour.split(",")]
        (cr, cg, cb), tol = parts[:3], (parts[3] if len(parts) > 3 else 60)
    return (abs(r - cr) < tol) & (abs(g - cg) < tol) & (abs(b - cb) < tol)


def components(mask, min_px=3):
    try:
        from scipy import ndimage
    except ImportError:  # pragma: no cover - host convenience
        sys.exit("needs scipy on the host: py.exe -3.12 -m pip install scipy")
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    out = []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        if len(xs) >= min_px:
            out.append((int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()), int(len(xs))))
    return sorted(out, key=lambda t: (t[2], t[0]))


def main(argv):
    shot, cmd = argv[1], argv[2]
    im = np.array(Image.open(shot).convert("RGB")).astype(int)
    if cmd == "zoom":
        x0, y0, x1, y1 = (int(v) for v in argv[3:7])
        out, f = argv[7], (int(argv[8]) if len(argv) > 8 else 3)
        Image.fromarray(im[y0:y1, x0:x1].astype(np.uint8)).resize(
            ((x1 - x0) * f, (y1 - y0) * f), Image.NEAREST).save(out)
        print("wrote", out)
        return
    if cmd == "comps":
        colour, rest = argv[3], argv[4:]
        axis = None
        if "--axis" in rest:
            i = rest.index("--axis")
            axis = float(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        x0, y0 = (int(rest[0]), int(rest[1])) if len(rest) >= 4 else (0, 0)
        x1, y1 = (int(rest[2]), int(rest[3])) if len(rest) >= 4 else (im.shape[1], im.shape[0])
        m = mask_for(im[y0:y1, x0:x1], colour)
        for cx0, cx1, cy0, cy1, n in components(m):
            cx0, cx1, cy0, cy1 = cx0 + x0, cx1 + x0, cy0 + y0, cy1 + y0
            c = (cx0 + cx1) / 2
            extra = f"  c-axis={c - axis:+.1f}" if axis is not None else ""
            print(f"x {cx0}-{cx1} (w={cx1 - cx0 + 1}) y {cy0}-{cy1} centre_x={c:.1f} n={n}{extra}")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
