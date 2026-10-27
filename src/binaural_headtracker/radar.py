"""Top-down azimuth-radar geometry for the calibration GUI (portable, B6.1).

The GUI's right panel is a bird's-eye "radar": the listener at the centre facing
*up* (screen −y = front), and the world-locked speakers placed around them. This
module is the pure geometry — azimuth → radar pixel, and which speaker the head is
most facing — so it is unit-tested with no camera/OpenCV; the drawing shell lives in
``apps/_console.py`` (and the Dear PyGui cockpit).

Azimuth convention matches the world model and `hrir`: ``az`` is degrees
counter-clockwise from front, ``+90`` = the left ear. On the radar, front is up and
``+az`` goes to the screen's **left** (so it reads like looking down on your own head).
Screen coordinates are the usual image ones: ``x`` right, ``y`` down.
"""

from __future__ import annotations

import math


def azimuth_to_radar_xy(
    az_deg: float, radius: float, cx: float = 0.0, cy: float = 0.0
) -> tuple[float, float]:
    """Radar pixel for an azimuth: front → up (−y), +az → left (−x).

    ``radius`` is the distance from the centre ``(cx, cy)``. ``az_deg=0`` → straight
    up; ``+90`` → screen-left; ``-90`` → screen-right; ``180`` → straight down.
    """
    r = math.radians(az_deg)
    return (cx - radius * math.sin(r), cy - radius * math.cos(r))


def nearest_front_channel(directions) -> int:
    """Index of the channel whose azimuth is closest to straight ahead (0°).

    ``directions`` is ``(C, 2)`` ``(az, el)`` in degrees (e.g. from
    `calibrate.directions_for_pose`). Distance is the wrapped angular difference, so
    350° counts as 10° from front. This is the real measured BRIR the head is most
    facing — the one the `nearest` renderer (B1.12) would select for the forward
    source — highlighted on the radar.
    """
    best, idx = math.inf, 0
    for i, row in enumerate(directions):
        az = float(row[0])
        d = abs(((az + 180.0) % 360.0) - 180.0)
        if d < best:
            best, idx = d, i
    return idx
