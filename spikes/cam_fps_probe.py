"""cam_fps_probe.py -- measure the ACTUAL frame throughput of each camera at several resolutions,
next to the driver's *claimed* fps (the cockpit's number). One camera at a time (no contention),
default MSMF backend (matches the live app). For each (camera, format): open fresh, warm up, then
time ~120 reads. Prints claimed vs measured so a lying "30 fps" readout is obvious.
"""
from __future__ import annotations

import sys
import time

import cv2

# (label, width, height, fps_request) -- 0 = leave the driver default
FORMATS = [
    ("default    ", 0, 0, 0),
    ("320x240@60 ", 320, 240, 60),
    ("640x480@30 ", 640, 480, 30),
    ("640x480@60 ", 640, 480, 60),
]
WARMUP = 15        # discard the ramp-up frames after open
MEASURE_FRAMES = 120
MEASURE_MAX_S = 4.0


def detect(max_index: int = 6) -> list[int]:
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            found.append(i)
        cap.release()
    return found


def measure(index: int, w: int, h: int, fps_req: int):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if w:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    if h:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    if fps_req:
        cap.set(cv2.CAP_PROP_FPS, fps_req)
    gw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    gh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    claimed = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    for _ in range(WARMUP):
        cap.read()
    t0 = time.perf_counter()
    n = 0
    while n < MEASURE_FRAMES and (time.perf_counter() - t0) < MEASURE_MAX_S:
        ok, _ = cap.read()
        if ok:
            n += 1
    elapsed = time.perf_counter() - t0
    cap.release()
    measured = n / elapsed if elapsed > 0 else 0.0
    return gw, gh, claimed, measured, n


def main() -> None:
    idx = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else detect()
    if not idx:
        print("no cameras opened")
        return
    print(f"cameras detected: {idx}   (default MSMF backend, one at a time)\n")
    print(f"{'cam':>3}  {'format':11}  {'negotiated':>12}  {'claimed':>8}  {'MEASURED':>9}  frames")
    print("-" * 62)
    for i in idx:
        for label, w, h, f in FORMATS:
            r = measure(i, w, h, f)
            if r is None:
                print(f"{i:>3}  {label}  (failed to open)")
                break
            gw, gh, claimed, measured, n = r
            flag = ""
            if claimed > 0 and measured < claimed * 0.75:
                flag = "  <- claim overstated"
            print(f"{i:>3}  {label} {gw:>5}x{gh:<4} {claimed:7.0f} {measured:8.1f} {n:5d}{flag}")
        print()
    print("MEASURED = real throughput (what the tracker actually gets). 'claimed' is CAP_PROP_FPS,")
    print("which is what the cockpit shows -- if MEASURED is well below it, the readout was lying.")


if __name__ == "__main__":
    main()
