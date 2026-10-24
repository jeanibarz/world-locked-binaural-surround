"""cam_phase_probe.py -- does running two webcams at once actually buy a higher effective pose rate?

Opens two cameras simultaneously (one capture thread each), stamps time.perf_counter() right after
every cap.read(), then reports whether their frames interleave (anti-phase -> ~2x, the win) or bunch
(in-phase -> wasted 2x the work for ~1x the rate), how that drifts over the run, and -- the number
that actually matters for latency -- the time-averaged "pose age" of the combined stream vs a single
camera. Run this BEFORE building any fusion: if the cameras sit in-phase, the rate idea is dead and
only coverage/robustness justifies a second camera.

Standalone (needs only cv2 + numpy). Run on the Windows host, with the cockpit CLOSED (so both
cameras are free). From WSL:
    py.exe -3.12 "$(wslpath -w spikes/cam_phase_probe.py)" --a 0 --b 1 --seconds 30
Pin a format on both if you want a fair test (and to fit two streams on the USB bus):
    ... --width 640 --height 480 --fps 30
"""
from __future__ import annotations

import argparse
import threading
import time

import cv2
import numpy as np


def _open(index: int, width: int, height: int, fps: int):
    """Open + configure one camera (in the MAIN thread, sequentially -- opening two MSMF cameras at
    the same instant is what made the earlier threaded version fail). Returns the cap or None."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # freshest frame, like the live app
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps:
        cap.set(cv2.CAP_PROP_FPS, fps)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    f = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    print(f"  camera {index}: open {w}x{h} @ {f:.0f}fps (negotiated)")
    return cap


def _reader(cap, stop: threading.Event, ready: threading.Barrier, out: list[float]) -> None:
    """Pure read loop -- no open/config here, so the two readers can start truly concurrently."""
    try:
        ready.wait(timeout=10)
    except threading.BrokenBarrierError:
        pass
    while not stop.is_set():
        ok, _ = cap.read()
        if ok:
            out.append(time.perf_counter())


def _mean_pose_age_ms(stamps: np.ndarray) -> float:
    """Time-averaged age of the most-recent pose for a consumer sampling at random times:
    E[residual] = sum(gap^2) / (2 * sum(gap)). For a constant period p this is p/2; large gaps
    (bunched frames) dominate quadratically, so this penalizes in-phase cameras correctly."""
    if stamps.size < 2:
        return float("nan")
    g = np.diff(stamps)
    return float((g @ g) / (2.0 * g.sum()) * 1000.0)


def _fps(stamps: np.ndarray) -> float:
    if stamps.size < 2:
        return 0.0
    return (stamps.size - 1) / (stamps[-1] - stamps[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=int, default=0, help="first camera index")
    ap.add_argument("--b", type=int, default=1, help="second camera index")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--fps", type=int, default=0)
    args = ap.parse_args()

    print(f"probing cameras {args.a} + {args.b} for {args.seconds:.0f}s "
          f"(close the cockpit first so both are free) ...")
    capA = _open(args.a, args.width, args.height, args.fps)
    time.sleep(0.5)                        # stagger the opens (concurrent MSMF opens fail)
    capB = _open(args.b, args.width, args.height, args.fps)
    if capA is None or capB is None:
        print(f"could not open both cameras (A={'ok' if capA else 'FAIL'}, "
              f"B={'ok' if capB else 'FAIL'}) -- aborting")
        for c in (capA, capB):
            if c:
                c.release()
        return
    stop = threading.Event()
    ready = threading.Barrier(2)            # the two reader threads start together
    ta: list[float] = []
    tb: list[float] = []
    th = [threading.Thread(target=_reader, args=(capA, stop, ready, ta), daemon=True),
          threading.Thread(target=_reader, args=(capB, stop, ready, tb), daemon=True)]
    for t in th:
        t.start()
    time.sleep(args.seconds)
    stop.set()
    for t in th:
        t.join(timeout=3)
    capA.release()
    capB.release()

    A = np.array(sorted(ta))
    B = np.array(sorted(tb))
    if A.size < 10 or B.size < 10:
        print(f"too few frames (A={A.size}, B={B.size}) -- check the camera indices")
        return

    # align both clocks to a common origin, merge
    t0 = min(A[0], B[0])
    A -= t0
    B -= t0
    fpsA, fpsB = _fps(A), _fps(B)
    pA = 1.0 / fpsA if fpsA else 0.0
    merged = np.array(sorted(np.concatenate([A, B])))
    gaps_ms = np.diff(merged) * 1000.0
    comb_fps = _fps(merged)

    # phase of each B frame within A's cadence (0.5 == perfect anti-phase == best interleave)
    phase = []
    ai = 0
    for b in B:
        while ai + 1 < A.size and A[ai + 1] <= b:
            ai += 1
        if pA > 0 and A[ai] <= b:
            phase.append(((b - A[ai]) / pA) % 1.0)
    phase = np.array(phase) if phase else np.array([0.0])
    # how close to 0 or 1 (in-phase) vs 0.5 (anti-phase): 0 == bunched, 1 == perfectly interleaved
    interleave = float(np.mean(1.0 - np.abs(phase - 0.5) * 2.0))

    # redundant = a combined gap much shorter than half a single period (two frames bunched)
    redundant = float(np.mean(gaps_ms < (pA * 1000.0 * 0.25))) if pA else float("nan")

    age_single = _mean_pose_age_ms(A)
    age_comb = _mean_pose_age_ms(merged)
    gain = age_single / age_comb if age_comb and not np.isnan(age_comb) else float("nan")

    # phase drift: mean interleave over 5 equal time bins across the run
    bins = 5
    span = merged[-1]
    drift = []
    for k in range(bins):
        lo, hi = span * k / bins, span * (k + 1) / bins
        sel = (B >= lo) & (B < hi)
        ph = phase[sel[:phase.size]] if phase.size == B.size else None
        if ph is not None and ph.size:
            drift.append(np.mean(1.0 - np.abs(ph - 0.5) * 2.0))
        else:
            drift.append(float("nan"))

    print("\n================ RESULT ================")
    print(f"camera {args.a}: {A.size} frames, {fpsA:5.1f} fps")
    print(f"camera {args.b}: {B.size} frames, {fpsB:5.1f} fps")
    print(f"combined stream: {comb_fps:5.1f} fps  "
          f"(rate x{comb_fps / max(fpsA, fpsB):.2f} vs the faster cam)")
    print(f"combined gaps (ms): p50 {np.percentile(gaps_ms, 50):.1f} | "
          f"p95 {np.percentile(gaps_ms, 95):.1f} | p99 {np.percentile(gaps_ms, 99):.1f} | "
          f"max {gaps_ms.max():.1f}")
    print(f"interleave quality: {interleave * 100:.0f}%  (100% = anti-phase, 0% = bunched)")
    print(f"redundant (bunched) frames: {redundant * 100:.0f}%")
    print("interleave over time (5 bins, start->end): "
          + " ".join(f"{d * 100:3.0f}%" for d in drift) + "   <- watch for drift")
    print(f"mean pose age:  single cam {age_single:5.1f} ms  ->  combined {age_comb:5.1f} ms"
          f"   (latency gain x{gain:.2f})")
    print("----------------------------------------")
    if gain >= 1.6:
        print("VERDICT: strong interleave -> two cameras give a real ~2x win. Fusion worth it.")
    elif gain >= 1.25:
        print("VERDICT: partial interleave -> a modest win, may drift. Fusion is marginal;")
        print("         weigh it against simply running one camera faster + prediction.")
    else:
        print("VERDICT: ~in-phase -> NO meaningful rate win (2x work, ~1x rate). Only pursue")
        print("         a second camera for COVERAGE/robustness, not refresh rate.")
    print("Note: USB webcams free-run; the phase can drift over minutes. Re-run to sample.")


if __name__ == "__main__":
    main()
