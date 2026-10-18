"""Real-time audio health + end-to-end latency estimate (B6.3).

Pure, headless functions that turn the numbers the live apps already hold — block size,
sample rate, measured render time per block, output-FIFO fill, device latencies, tracker
rate, recent dropouts — into something a person can read: how much of the per-block
real-time budget the render is burning, an **estimate of the head-move-to-ear delay**
(with its caveats stated), and a plain verdict (healthy / tight / at risk / failing) with
the reasons behind it. This is the brain of the GUI diagnostics panel; the drawing is
host-side, but the maths here is unit-tested.

The model, stage by stage, for the end-to-end "I turn my head → I hear it move" delay:

* the camera only reports a *new* pose every ``1/rate`` s, and a frame is already ~one
  interval old when it arrives → ``~1.5 × pose_interval`` of tracking age (the capture
  exposure itself is not measurable from software, so this is an estimate);
* the render worker turns the newest pose into audio within ~one ``block``;
* that audio then waits in the output FIFO (``fifo_fill × block``) and the device's own
  output buffer before it reaches the ear.

So the reported estimate is a sum of measured stages plus a labelled tracking estimate, not
a claim of exact physical latency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Health(str, Enum):
    """Plain real-time verdict, worst-first when compared by :pyattr:`rank`."""

    OK = "healthy"
    TIGHT = "tight"
    RISK = "at risk"
    FAILING = "failing"

    @property
    def rank(self) -> int:
        return {"healthy": 0, "tight": 1, "at risk": 2, "failing": 3}[self.value]


@dataclass(frozen=True)
class Diagnosis:
    """A read-out of real-time health and estimated latency (all times in ms)."""

    health: Health
    reasons: tuple[str, ...]
    block_size: int             # render block in samples (what the user is actually running)
    samplerate: int             # render sample rate (Hz)
    render_label: str           # human render config: engine · method · grid · test state
    block_ms: float             # one render block's duration (the real-time budget)
    render_ms: float            # mean render time per block (whole render: 7.1 + test source)
    render_ms_peak: float       # worst recent render time per block
    render_ms_test: float       # of render_ms, the part spent on the test source (0 if off)
    render_load: float          # render_ms / block_ms (mean), 1.0 = exactly real time
    render_load_peak: float     # render_ms_peak / block_ms
    render_load_test: float     # render_ms_test / block_ms (the test source's share)
    audio_out_latency_ms: float # a just-rendered sample's wait: FIFO fill + device out buffer
    tracking_ms: float          # estimated pose age (camera → usable pose)
    end_to_end_ms: float        # estimated head-move → ear (sum of stages below)
    recent_underruns: int
    fifo_fill_blocks: float
    cushion_blocks: int
    # camera / tracker pipeline
    loop_ms: float              # one tracker loop (camera read + detect + draw)
    detect_ms: float            # face-detection time per frame
    camera_fps: float           # the camera driver's reported frame rate
    cam_w: int
    cam_h: int
    tracking_note: str          # plain-language note on what limits the tracker rate


def classify(
    *,
    block_size: int,
    samplerate: int,
    render_ms: float,
    render_ms_peak: float,
    fifo_fill_blocks: float,
    cushion_blocks: int,
    render_ms_test: float = 0.0,
    render_label: str = "",
    output_latency_ms: float = 0.0,
    input_latency_ms: float = 0.0,
    pose_rate_hz: float = 0.0,
    recent_underruns: int = 0,
    detect_ms: float = 0.0,
    camera_fps: float = 0.0,
    cam_w: int = 0,
    cam_h: int = 0,
) -> Diagnosis:
    """Diagnose real-time health + estimate end-to-end latency from live numbers.

    ``render_ms`` / ``render_ms_peak`` are the mean / worst render time for one ``block_size``
    block (measured on the worker). ``fifo_fill_blocks`` is the current output FIFO fill;
    ``cushion_blocks`` the target. Latencies are the device buffers (ms). ``pose_rate_hz`` is
    the tracker update rate; ``recent_underruns`` the output dropouts counted in the last short
    window. All inputs are clamped defensively so a cold start (zeros) yields a sane result.
    """
    block_ms = 1000.0 * block_size / samplerate if samplerate > 0 else 0.0
    render_ms = max(0.0, render_ms)
    render_ms_peak = max(render_ms, render_ms_peak)
    load = render_ms / block_ms if block_ms > 0 else 0.0
    load_peak = render_ms_peak / block_ms if block_ms > 0 else 0.0
    render_ms_test = min(max(0.0, render_ms_test), render_ms)
    load_test = render_ms_test / block_ms if block_ms > 0 else 0.0

    # A just-rendered sample waits: whatever is queued in the FIFO, then the device buffer.
    audio_out_ms = max(0.0, fifo_fill_blocks) * block_ms + max(0.0, output_latency_ms)
    # Tracking age: a frame is ~one interval old when it arrives and the next is one interval
    # away → ~1.5 intervals. Unmeasurable camera exposure is folded into this estimate.
    pose_interval_ms = 1000.0 / pose_rate_hz if pose_rate_hz > 0 else 0.0
    tracking_ms = 1.5 * pose_interval_ms
    end_to_end_ms = (
        tracking_ms + max(0.0, input_latency_ms) + block_ms + audio_out_ms
    )

    reasons: list[str] = []
    health = Health.OK
    if recent_underruns > 0:
        health = Health.FAILING
        reasons.append(f"{recent_underruns} dropout(s) just now — the render missed the deadline")
    if load_peak > 1.0:
        health = Health.FAILING
        reasons.append(f"a render spike hit {load_peak * 100:.0f}% of the block budget (over 100%)")
    elif load_peak > 0.85:
        health = max(health, Health.RISK, key=lambda h: h.rank)
        reasons.append(
            f"render spikes reach {load_peak * 100:.0f}% of the budget — little headroom")
    if load > 0.6 and health.rank < Health.RISK.rank:
        health = max(health, Health.TIGHT, key=lambda h: h.rank)
        reasons.append(f"render averages {load * 100:.0f}% of the per-block budget")
    # When the budget is tight and the test source is a meaningful share, say so — it is the
    # one piece the user can drop with T, and A/B-ing while music plays is what tips it over.
    if load_test > 0.1 and load_peak > 0.85:
        reasons.append(
            f"the test source adds ~{load_test * 100:.0f}% — turn it off (T) for the daily mix, "
            "or raise --block / use --rust for A/B-while-playing headroom")
    if cushion_blocks > 0 and fifo_fill_blocks < 1.0 and health is Health.OK:
        # Buffer nearly empty but not yet dropping: worth noting as "tight".
        health = Health.TIGHT
        reasons.append("output buffer is nearly empty (close to a dropout)")
    if not reasons:
        reasons.append(
            f"render uses {load * 100:.0f}% of the budget (peak {load_peak * 100:.0f}%); "
            "buffer steady, no dropouts"
        )

    # What limits the tracker rate? (detection vs low light vs the camera's own fps)
    loop_ms = 1000.0 / pose_rate_hz if pose_rate_hz > 0 else 0.0
    if pose_rate_hz <= 0:
        tracking_note = ""
    elif detect_ms > 0 and detect_ms > 0.6 * loop_ms:
        tracking_note = (f"face detection is the limit (~{detect_ms:.0f} ms/frame); "
                         "camera resolution won't change it")
    elif camera_fps > 0 and pose_rate_hz < 0.6 * camera_fps:
        tracking_note = (f"frame rate has dropped below the camera's {camera_fps:.0f} fps — "
                         "likely low light (auto-exposure); add light or fix the exposure")
    elif camera_fps > 0 and pose_rate_hz >= 0.85 * camera_fps:
        tracking_note = (f"at the camera's {camera_fps:.0f} fps — its hardware rate; "
                         "a faster webcam is the only way to more")
    elif camera_fps > 0:
        tracking_note = (f"a little below the camera's {camera_fps:.0f} fps — "
                         "capture/draw overhead, not detection")
    else:
        tracking_note = f"tracker ~{pose_rate_hz:.0f} Hz"

    return Diagnosis(
        health=health,
        reasons=tuple(reasons),
        block_size=int(block_size),
        samplerate=int(samplerate),
        render_label=render_label,
        block_ms=block_ms,
        render_ms=render_ms,
        render_ms_peak=render_ms_peak,
        render_ms_test=render_ms_test,
        render_load=load,
        render_load_peak=load_peak,
        render_load_test=load_test,
        audio_out_latency_ms=audio_out_ms,
        tracking_ms=tracking_ms,
        end_to_end_ms=end_to_end_ms,
        recent_underruns=recent_underruns,
        fifo_fill_blocks=fifo_fill_blocks,
        cushion_blocks=cushion_blocks,
        loop_ms=loop_ms,
        detect_ms=max(0.0, detect_ms),
        camera_fps=max(0.0, camera_fps),
        cam_w=int(cam_w),
        cam_h=int(cam_h),
        tracking_note=tracking_note,
    )
