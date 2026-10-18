# Cumulative learnings

Durable, compounding lessons, newest appended at the end, each tagged with the
journal entry it came from. This is the quick reference; the entries carry the
full story.

1. **The core mechanic is world-lock: fix the virtual speakers in the world, move
   the convolution with the head.** Head rotation must change the per-speaker
   direction (and thus the HRIR), not the scene. Every component (virtual device,
   EQ, GUI, AI calibration) serves that single perceptual idea.
   (→ [00](00-problem-and-objective.md))
2. **Committed prototype stack:** `uv` (lockfile tracked) + setuptools + `src/`
   layout + `pytest` + `ruff` (lint `E,W,F,I,UP,B`), Python `>=3.10,<3.13`,
   baseline deps `numpy`/`scipy` only. Mirrors the sibling `binaural-localization`
   for zero-friction maintenance across both repos. (→ [01](01-python-project-setup.md))
3. **Keep platform-specific deps out of the baseline.** Audio (sounddevice) and
   vision (mediapipe) deps join at the spike block that first needs them, each
   logged with its licence, so `uv sync`/CI stay fast and green on headless WSL.
   (→ [01](01-python-project-setup.md))
4. **No open-source licence yet** (donationware decision pending, requirements
   § 9): no `LICENSE`/`license` field; a `pyproject.toml` comment guards against a
   later block adding one. All deps so far are permissive (BSD/MIT/Apache), no GPL.
   (→ [01](01-python-project-setup.md))
5. **The dev box is WSL2, but the Windows host is reachable via interop.**
   `py.exe -3.12` runs a native Windows Python (with `sounddevice`/WASAPI and the
   webcam); run repo scripts by their `wslpath -w` path. So Windows-only spikes
   (audio, camera) are runnable from this box — never record them "blocked on WSL".
   Keep portable logic in the package (unit-tested on WSL), hardware I/O in
   `spikes/`. (→ [02](02-audio-io-spike.md))
6. **WASAPI reality on this host:** ~22 ms reported latency (shared) on the
   default output, which is a **VoiceMeeter virtual sink** — exclusive mode is no
   better and the bare hardware DAC can't be opened directly (VoiceMeeter owns
   it). The <30 ms target needs the virtual chain bypassed (B4/B8). A *measured*
   round-trip needs a loopback the virtual device must expose (B0.3). Resolve
   device indices as runtime *roles*, never hard-code them. (→ [02](02-audio-io-spike.md))
7. **Language is not locked to Python.** Python-first for iteration, but a block
   may prototype/benchmark a hot path in C/C++/Rust as soon as performance is the
   deciding question — don't defer all native work to M8. Record the language
   choice + its licence in the journal; keep config/HRIR/world-model shared across
   languages. (→ AGENTS.md "Working discipline")
8. **A virtual 7.1 sink on Windows = VoiceMeeter VAIO (8-ch); VB-CABLE is stereo
   only.** Apps select `VoiceMeeter Input (VAIO)` as their 7.1 speakers; the
   renderer captures `VoiceMeeter Output (VAIO)`. Resolve both by name/channels at
   runtime on the **WASAPI** host API only — the same cable also appears under
   MME/DirectSound/WDM-KS with different latency, and **MME truncates device names
   to 31 chars** (`MAXPNAMELEN`), breaking name matching. Pair a cable's two halves
   by their shared trailing parenthetical. (→ [03](03-virtual-device-spike.md))
9. **A VoiceMeeter loopback needs the virtual strip routed to a B bus.** The
   capture endpoint opens and reads frames regardless, but stays silent until the
   strip is assigned to B1 — report that as a config gap, never a spurious 0 ms. A
   dedicated **VB-CABLE** gives a zero-config internal loopback for a clean latency
   reference. The reported bound on the VoiceMeeter chain is ~44 ms (22 out + 22
   in), above the <30 ms target. (→ [03](03-virtual-device-spike.md))
10. **Windows-console spikes: force UTF-8 stdout and keep output ASCII.** The
    default cp1252 codec aborts the whole run on a stray non-ASCII char (`→`); a
    `sys.stdout.reconfigure(encoding="utf-8")` guard plus ASCII-only notes keeps
    the RESULT_JSON line from being lost. (→ [03](03-virtual-device-spike.md))
11. **VoiceMeeter / VB-CABLE are proprietary donationware, not redistributable.**
    Depend on them at runtime only; the user installs them. Bundle nothing, add no
    Python dependency, introduce no GPL — § 9 safe. (→ [03](03-virtual-device-spike.md))
12. **Loopback latency probes must record *concurrently* with playback.** Writing
    the whole output buffer before reading lets the input ring overflow, so a short
    sweep is dropped before the first read and reads as silence — while a
    *continuous* tone hides the bug. Interleave block-writes with reads, or use one
    full-duplex stream. (→ [04](04-measured-roundtrip-latency.md))
13. **The measured virtual round-trip ≈ the VAIO/AUX driver buffer:** 6144 samples
    = 128 ms → ~130 ms measured, with the engine and a 1024-sample hardware output
    negligible beside it. Latency on the virtual path tracks that one buffer; the
    <30 ms budget needs it driven down and/or the mixer bypassed (B0.5/B8). Two
    independent streams make the figure a ±~20 ms ballpark (start skew + polling);
    sample-accuracy needs a single-clock full-duplex stream.
    (→ [04](04-measured-roundtrip-latency.md))
14. **Use an unused VoiceMeeter input strip (AUX) → a B-bus as an isolated test
    tap.** Measurement sweeps stay off the listening strip (VAIO→A1), so nothing
    reaches the headphones and the normal routing is untouched.
    (→ [04](04-measured-roundtrip-latency.md))
15. **MediaPipe FaceLandmarker → head pose is easy and cheap.** Its 4x4
    facial-transformation matrix gives the head orientation directly (no manual
    solvePnP); scale-normalise the 3x3 block and decompose to Euler. CPU inference
    is ~12 ms with a face / ~3 ms idle (p95 ~15 ms) — far inside the <50 ms
    tracking budget. Apache-2.0; the `.task` model is user-fetched, not bundled.
    (→ [05](05-head-tracker-spike.md))
16. **For head tracking the camera is the bottleneck, not the model.** The C270 at
    640×480 updates only ~15 fps (~66 ms/frame), which alone nears the <50 ms
    tracking ceiling. Raise the *update rate* (MJPG fourcc, lower resolution,
    faster camera) before optimising inference. (→ [05](05-head-tracker-spike.md))
17. **Resolve the webcam by looking, not by index, and warm the sensor up.**
    Virtual cameras (OBS Virtual Camera) masquerade as real ones at a low index;
    save a frame and inspect it. A USB webcam also returns dark/garbage frames for
    ~1–2 s after opening — drain them (a timed settle) and ensure light, or you get
    zero detections. (→ [05](05-head-tracker-spike.md))
18. **FaceLandmarker is reliable to ~±50° yaw and loses the face beyond it** (it is
    a near-frontal model); detection gaps cluster exactly at the extremes, so judge
    pose coverage from the *full* series, not a few early frames. The product
    operating range is therefore **±45° yaw / ±45° pitch** (requirements FR-4);
    past that is graceful-loss/re-acquisition (NFR-5, B4.2) or multi-camera
    (FR-13). (→ [05](05-head-tracker-spike.md))
19. **SOFA is just HDF5 — read it with h5py, no SOFA library** (mirrors the
    sibling). SimpleFreeFieldHRIR: `Data.IR` (M, 2, N) with receiver 0 = left,
    1 = right; `SourcePosition` (M, 3) spherical `(az, el, radius)` degrees;
    `Data.SamplingRate`. Azimuth is CCW from front (90° = left) — validated on MIT
    KEMAR where az 90° makes the ipsilateral ear ~4× louder (head shadow). Resample
    IRs with `resample_poly` using GCD-reduced up/down. Keep real `.sofa` out of
    git; test with a synthetic one. (→ [06](06-hrir-sofa-loader.md))
20. **The owner's tuned surround is 8 fixed-direction baked BRIRs** (per 7.1
    speaker, per ear), good for a **static** renderer but not the continuous field
    head-tracking needs. Static render = per-channel convolve + per-ear sum (the
    EqualizerAPO config's operation); the comp FIR + parametric EQ are a later
    post-stage (M5/B5). Verify such a renderer **by construction** (impulse on a
    channel → that channel's exact BRIR) and sanity-check real data by **ITD sign**
    — ILD is unreliable here because the owner's real-head BRIRs are left/right
    asymmetric. (→ [07](07-static-binaural-renderer.md))
21. **Interpolate HRIRs by time-alignment, never by averaging samples.** Summing
    two IRs whose arrivals differ by a delay comb-filters them (periodic magnitude
    nulls) and destroys the ITD. Instead estimate each neighbour's broadband delay
    *per ear*, advance each to a common origin, blend the aligned IRs, then
    re-apply the weighted-mean delay — the ITD is carried as an explicit, smoothly
    interpolated delay. On KEMAR leave-one-out this is ~2.7x closer to the true
    measured IR than naive averaging (2.50 vs 6.81 dB LSD). k-NN inverse-distance
    weights (p=2) suffice for M1 and need no sphere triangulation; VBAP/barycentric
    is a later refinement (B1.8). (→ [08](08-direction-interpolation.md))
22. **Estimate an IR's arrival delay from the Hilbert (analytic-signal) envelope
    peak, not argmax(|ir|).** A real HRIR has several comparable sample peaks
    (pinna lobes), so the raw peak hops between them and the derived ITD jumps
    (8 samples across 5 deg on KEMAR — an artefact); the envelope has one smooth
    broadband maximum and the ITD comes out monotone (0 -> ~35 smp / 0.73 ms over
    0->90 deg). The raw-peak estimator passed every *synthetic* delta test and only
    failed on real data — validate delay estimators on measured HRIRs.
    (→ [08](08-direction-interpolation.md))
23. **Uniformly-partitioned overlap-save (UPOLS) makes block size a free latency
    knob.** Chop the filter into `P = ceil(N/B)` parts of `B` taps, pre-FFT each to
    `2B` points, keep a frequency-domain delay line of the last `P` input windows,
    and per block do one `2B` FFT + `P` complex MACs + one `2B` IFFT. Per-block
    cost is `O(P)` MACs plus two fixed FFTs — constant in the filter length for a
    fixed block — and the only latency is the one block of input buffering (no
    extra algorithmic delay). (→ [09](09-partitioned-convolution.md))
24. **In a Python convolution prototype the bottleneck is per-call overhead, not
    arithmetic.** An 8-channel 7.1→stereo mix (16 convolvers × up to 75 partitions)
    runs only 1.0×RT at a 128-sample block but 3.2×RT at 256, because the cost is
    the number of Python iterations (channels × partitions), not FLOPs. Larger
    blocks trade latency for fewer, bigger iterations; vectorising the MAC/FFT
    across partitions and channels is the lever to pull before going native.
    (→ [09](09-partitioned-convolution.md))
25. **Overlap-save (FFT `2B`, hop `B`) puts the alias-free linear-convolution
    samples in the second half of each IFFT, and the streamed result equals the
    offline `scipy.signal.fftconvolve` to ~1e-14.** That exact equivalence is a
    cheap correctness oracle for any future convolution refactor (native port,
    vectorisation): stream a random signal + flush, diff against `fftconvolve`.
    (→ [09](09-partitioned-convolution.md))
26. **One world frame for geometry *and* HRIR lookup: `+x=front, +y=left,
    +z=up`.** Then `az = atan2(y, x)` (CCW from front, +90° = left ear) and
    `el = asin(z/r)` match the SOFA/HRIR azimuth convention (L19) and the nominal
    `SPEAKER_LAYOUT_7P1`, so world positions project straight to the `(az, el)`
    the HRIR lookup wants with no conversion layer. Return signed azimuth in
    `(-180, 180]` to round-trip the signed layout; the lookup mods to `[0, 360)`.
    (→ [10](10-world-layout-model.md))
27. **Build the fixed-head layout as the full world-lock projection, not a
    special case.** `d_i = Rᵀ(p_i − p_h)` with head rotation `R` and position
    `p_h` as defaulted arguments (identity / origin = neutral head); the fixed
    head is just the default, and M3/B3.1 couples the tracker by *passing a
    matrix* — no rewrite. The effect is then unit-testable before any audio: a
    head yaw of +θ shifts every speaker azimuth by −θ. Keep the Euler→`R` mapping
    **out** of this module — sign/axis and the neutral pose bind to the tracker
    and belong to calibration (B2.2/B3.1, per `head_pose.py`).
    (→ [10](10-world-layout-model.md))
28. **Verify a geometry module by two independent routes to the same numbers.**
    The projected fixed-head angles must equal the hand-written nominal layout
    (`SPEAKER_LAYOUT_7P1`), and `direction_to_unit`/`unit_to_direction` must
    round-trip — a coordinate bug desynchronises the two routes. Distance/scale
    being direction-invariant for a centred head is a third cheap invariant.
    (→ [10](10-world-layout-model.md))
29. **Integration blocks that join well-factored modules are mostly wiring.**
    The B1.5 static renderer is one direction-lookup loop —
    `layout.head_relative_directions()` → `HRIRSet.interpolated_irs()` → a
    per-channel `(C, N)` IR bank → the shared convolve-and-sum — because
    B1.1–B1.4 were each built to the same `(az, el)` / `(C, N)` contract. Design
    the seams up front and the join needs no new DSP. Extract the shared kernel
    (`surround.convolve_and_sum_stereo`) so the baked (B1.6) and SOFA (B1.5)
    paths don't duplicate the convolution loop. (→ [11](11-static-sofa-renderer.md))
30. **Make the offline renderer and the real-time engine the same computation and
    assert it.** B1.5's offline batch render equals the B1.3 `StereoConvolver`
    block stream to 1e-9, so the simple offline path is a ground-truth oracle for
    the harder-to-inspect streaming path and any future divergence (native port,
    vectorisation) is caught immediately. (→ [11](11-static-sofa-renderer.md))
31. **A fresh `git worktree` has no `.venv`: run `uv sync --extra dev` before
    `uv run pytest`.** Otherwise `uv run pytest` composes a transient env that
    silently drops baseline deps (e.g. `h5py`) and fails with a misleading
    `ModuleNotFoundError`. It is a toolchain artifact, not a code regression —
    verify against a known-good checkout before suspecting the diff.
    (→ [11](11-static-sofa-renderer.md))
32. **A filter applied equally to both ears preserves the interaural cues exactly
    — it re-timbres without changing the image geometry.** The owner's big
    subjective EQ is under `Channel: L R` (same biquads on L and R), so it leaves
    ITD/ILD untouched (0.16 dB L-vs-R asymmetry). **But "interaural geometry
    unchanged" is NOT "perceived image unchanged":** a common filter still shifts
    the perceived image by reweighting direct-vs-diffuse energy (entry 13). Only a
    *per-ear-asymmetric* stage changes the geometry; but *perceived* spatial
    stability also depends on the direct/reverb balance, which a both-ears EQ moves.
    (→ [12](12-eqapo-offline-comparison.md), [13](13-eq-stabilises-via-drr.md))
33. **Verify a diagnosis against ground truth — and make sure your metric measures
    what the human actually perceives.** Entry 12 "verified" the EQ's effect with an
    interaural ITD/coherence metric and concluded the EQ doesn't steady the image;
    the owner then *listened* and heard the opposite. The metric measured interaural
    *geometry* (correctly unchanged) but not perceived stability, which tracks the
    direct-to-reverberant ratio (entry 13). Reproducing the real chain offline
    (reuse the sibling engine; depend, don't duplicate) is necessary but not
    sufficient — a wrong proxy gives a confident wrong answer. Let the ear arbitrate,
    then find the metric that agrees with it.
    (→ [12](12-eqapo-offline-comparison.md), [13](13-eq-stabilises-via-drr.md))
34. **For a reverberant source, perceived image stability ≈ direct-to-reverberant
    ratio (DRR), and a both-ears EQ moves it.** The diffuse reverb tail has no fixed
    direction, so when it is loud relative to the direct hit the image wanders; the
    owner's EQ (big HF boost) lifts the broadband direct transient far more than the
    HF-poor diffuse tail, raising DRR (+1.9 dB mean, +5.1 dB on rear-right, whose
    bake tail was *louder* than its direct sound) and steadying the image — without
    making the tail directional. Two independent levers on the wander: raise DRR
    (EQ/comp, B5) and/or separate direct from tail (B1.7).
    (→ [13](13-eq-stabilises-via-drr.md))
35. **"Depend, don't duplicate" is about logic/research, not a small standard
    kernel — and the runtime *shape* matters.** The sibling's EqualizerAPO engine is
    faithful but *offline* (impulse reconstruction) and drags TensorFlow+soundfile;
    the product needs a *live, dep-light block-processor*. So we **ported** the RBJ
    biquad math (a ~60-line standard kernel) into `eq.py` rather than importing the
    package, and kept the sibling for offline verification only. Make such a port
    literal and checkable: byte-exact parity (0.0 coeff diff on all 70 of the
    owner's real filters) proves the semantics are identical, so reuse ≡ port here.
    (→ [14](14-parametric-eq-engine.md))
36. **Interpolate a reverberant BRIR by splitting direct from tail: align/blend the
    direct, hold the tail.** The direct wavefront carries the direction (ITD/ILD),
    so time-align it to a common origin, magnitude-blend the neighbours, and re-apply
    the *interpolated* delay (resynthesised ITD). The diffuse tail has no direction —
    blend it **in place**; time-aligning it (shifting two different room tails and
    summing) comb-filters. Blend the tail with **equal-power** weights (`sqrt(w)`),
    not linear: the tails are decorrelated, so a linear `w_lo+w_hi=1` sum is a *power*
    sum that dips ~3 dB at the midpoint (the room audibly dims between speakers);
    the coherent direct, by contrast, uses linear weights. This renders a *single*
    clean phantom at the true angle,
    unlike amplitude-panning two whole BRIRs (two directions + two tails at once),
    which is why it localises across wide gaps where panning goes diffuse. (B1.7,
    the sparse-baked counterpart of B1.2's dense-SOFA interpolation.)
    (→ [15](15-baked-brir-interpolation.md))
37. **Depend on *validated* sibling artifacts through a registry with provenance,
    not by filename.** A dozen un-annotated `saved_model/*.keras` made "use the best
    mixed-rig judge" a guess — and a wrong guess gives confidently-wrong scores. The
    fix is a contract: the producer publishes a model only once trained+validated,
    with metadata (rigs, metrics, provenance) + an `index.json` "best"; the consumer
    resolves it (`judge_registry.best_judge()`) and never hard-codes a path. Keep the
    consumer pure-python so importing it doesn't drag the producer's ML deps.
    (→ [16](16-judge-model-registry.md))
38. **To interpolate an HRTF comb-free AND keep externalisation, warp the notch and
    reuse the EXCESS phase.** Slide the single pinna notch from each neighbour's
    frequency to the interpolated one (frequency-warped magnitude → one moving notch,
    not two), and reconstruct as minimum-phase-of-that-magnitude × the nearer
    neighbour's *excess* (all-pass) phase. Plain min-phase is comb-free but discards
    excess phase → flat/"less distant" (owner-rejected); keeping the *full* phase
    re-imposes that neighbour's own notch; the excess phase is the part that carries
    externalisation without the magnitude coupling. notchwarp beat timealign/minphase
    by the judge and the ear. (→ [17](17-interpolation-method-and-the-back-gap.md))
39. **Some "rendering" defects are data defects — triangulate before polishing the
    algorithm.** The rotation confusion survived every interpolation method because
    the owner's measured *rear* BRIRs are themselves front/back-ambiguous (judge +
    sibling entries 18/19): no blend of ambiguous endpoints yields a correct back
    cue. The judge made this visible and method-independent. When multiple methods
    all fail the same way, suspect the inputs, not the method — the fix moves to
    better rear cues / HRTF selection, not more interpolation.
    (→ [17](17-interpolation-method-and-the-back-gap.md))
40. **A stable image with a small azimuth error beats one that jumps hemispheres —
    front↔back flips are the worst error class.** Spectral HRTF interpolation buys a
    smooth phantom at the cost of occasional confident front↔back flips (it perturbs
    the pinna-notch cue that *is* the front/back cue); the owner heard those rapid
    front/back jumps as far more "wrong" than steady, slightly-off placement. Prefer
    a method that never flips even if it is a little less "accurate" on paper. This is
    why `nearest` (real measured BRIR, no synthesis) beat every interpolation by both
    the mixed-rig judge and the ear. (→ [18](18-nearest-default-no-flips.md))
41. **For a sparse measured set, prefer the nearest REAL response + a render-layer
    crossfade over synthesising in-between spectra.** Real BRIRs carry real cues (the
    judge rewards it, the ear confirms it); a synthesised in-between spectrum belongs
    to no real direction and misreads. Make smoothness the *renderer's* job (crossfade
    between successive real BRIRs as the source moves), not the per-azimuth lookup's.
    Also: do not lock a default on a *generic* judge + a partial ear test — wait for
    the owner's best (mixed-rig) judge and a full A/B; entry 17's notchwarp default was
    reversed once they were in. (→ [18](18-nearest-default-no-flips.md))
42. **For a device-driven component, split the device from the consumer and test the
    consumer with a recorded clip + timestamp-driven time.** `HeadTracker` takes an
    abstract pose *source* iterator: the webcam→MediaPipe source lives in a host spike,
    while the threaded smoothing/publishing consumer is unit-tested on in-memory
    `HeadPose` clips. Drive all timing from each sample's own timestamp (the 1€ filter
    and the rate/jitter stats), so the tests are deterministic and need **no real
    sleeps** and no hardware — and a clean `stop()` on an endless generator is testable.
    (→ [19](19-head-tracker.md))
43. **Build the world head-rotation from per-axis angle *deltas from neutral*, mapped
    to world axes, and defer the camera's sign to a host flag.** calibrate takes
    `(yaw,pitch,roll)` relative to a neutral "screen-centre" pose and composes
    `Rz(Δyaw)·Ry(Δpitch)·Rx(Δroll)` with yaw→world-up, pitch→world-left,
    roll→world-front — so neutral = identity and +yaw shifts every azimuth by −yaw
    (the B1.4 world-lock property), unit-testable headless. Which way the *camera*
    calls "+yaw" is hardware-dependent: expose `yaw_sign`/`pitch_sign`/`roll_sign`
    (default +1) and confirm them once on the host, rather than hard-coding a guess.
    (→ [20](20-calibration-camera-to-world.md))
44. **Split a GUI into tested geometry + a thin host drawing shell.** The calibration
    console's azimuth-radar math (`radar.azimuth_to_radar_xy`, `nearest_front_channel`)
    lives in `src/` with unit tests; the OpenCV window (`apps/calibration_gui.py`) only
    *draws* — composing the tested `radar` + tracker + `calibrate` + `world`. So the
    part that can be wrong (coordinate mapping, nearest selection) is covered headless,
    and the uncoverable part (camera + a cv2 window) stays a minimal host shell. Mock
    the whole screen first (the owner picked layout A from a high-fidelity mockup)
    before writing pixels. (→ [21](21-calibration-gui.md))
45. **Crossfade a time-varying convolution on the input *gain* into persistent
    per-filter voices — don't swap the filter.** Hard-swapping the active BRIR clicks
    and jumps the long room tail. Instead keep one continuous `StereoConvolver` per BRIR
    and, when the nearest changes, ramp the source's input gain from the old voice to
    the new over a few ms; the retired voice keeps convolving the trailing zeros so its
    tail rings out naturally and is reaped once drained. This is physically correct
    (each voice is an uninterrupted convolution) and click-free: on a steady tone the
    worst sample-to-sample step at the swap drops to the tone's *own* slew (≈3.8× below a
    hard switch) and plateaus by ~5 ms, so there is no artifact above the signal. The
    pool stays cheap — steady state runs 1 voice, a transition overlaps 2, never more.
    Start the first voice at full gain (a fade-in from silence would make startup differ
    from a plain convolution for no gain). Keep the world-lock geometry out of the
    renderer: pass it only the source's head-relative azimuth, computed in the tested
    `calibrate`/`world` layer via a one-"speaker" `source_layout`. (→ [22](22-live-world-lock.md))
46. **A real-time audio callback must not assume a fixed buffer size, and must not let an
    exception escape.** WASAPI (and most backends) call back with a *variable* frame count,
    not your `blocksize`. If the callback assumes `frames == block_size` it raises, and a
    raised callback **silently aborts** the PortAudio stream — the classic "video runs, no
    sound." Decouple the engine's fixed processing block from the callback: render fixed
    blocks into a small `pending` buffer and serve whatever `frames` are requested; wrap the
    body in try/except and surface the error to the main loop (zero-fill the output) instead
    of dying mutely. This only ever showed on a real device — headless tests all passed.
    (→ [23](23-device-pickers-and-host-bringup.md))
47. **Estimate head pose from the un-mirrored frame; mirror only the display.** A webcam
    preview is flipped so it reads like a mirror, but feeding that flipped frame to the pose
    estimator **negates yaw** — so the world-lock reverses and the source *tracks the head*
    instead of staying fixed. Detect on the true frame and flip only what's drawn (flip the
    landmark x if there's an overlay). Keeps the `yaw_sign=+1` convention (L43) consistent
    and fixes the sign at the root rather than papering over it with a flag. Predicted by the
    B6.1 review of the un-tested cv2 shell; confirmed only by a live head turn on the host —
    hardware is the only real test of a sign. (→ [23](23-device-pickers-and-host-bringup.md))
48. **To capture system 7.1, loopback the render endpoint — don't read the mixer's output
    bus.** A virtual mixer (VoiceMeeter) downmixes its *output* bus to stereo before exposing
    it, so reading that as a capture device silently loses centre/LFE/surrounds (measure
    per-channel RMS and only L/R carry signal). WASAPI **loopback** (`pyaudiowpatch` on
    Windows) taps the raw stream apps send *into* the render endpoint, before any downmix —
    the true 8 channels. Confirm the layout by measurement, not by the device's channel count.
    (→ [24](24-voicemeeter-passthrough.md))
49. **Reformulate a time-varying nearest-filter crossfade as a mix into fixed-filter buses, so
    the convolution is batched and GPU-friendly.** Steering N sources each through their
    current nearest BRIR (a per-source voice pool, L45) is, because convolution is linear,
    identical to: crossfade the N inputs into N **fixed-BRIR buses** (each input's gain ramping
    toward a one-hot on its current bus), then convolve each bus with its *fixed* filter and
    sum. The gains move, the filters never do — so the convolution becomes one batched
    partitioned overlap-save the GPU runs in a single shot. Prove the two forms equal offline
    (here 1e-9 over a head sweep) so only the batched conv is host-only; the routing/crossfade
    stays tested numpy. Needed because the full 8ch × 500 ms-BRIR render is ~0.97× RT on the
    CPU and ~6× RT on the GPU. (→ [24](24-voicemeeter-passthrough.md))
50. **Bridge two independent audio clocks with a primed, bounded FIFO.** Capture (loopback)
    and playback run on separate clocks and deliver in bursts. Render in the input callback
    (it owns the freshest head pose), push into a FIFO primed with a silence cushion that
    absorbs the jitter, and drain it in the output callback (zero-fill on underrun). Cap the
    backlog at a few cushions and drop oldest only on real drift, so latency stays bounded
    instead of growing. A safe first duplex; a native low-latency path comes later.
    (→ [24](24-voicemeeter-passthrough.md))
51. **Run GPU host Python with the cwd on a real drive, not the WSL mount.** CuPy's
    `_get_cuda_path` falls back to `os.path.exists('/usr/local/cuda')`; from a
    `\\wsl.localhost\…` cwd that path resolves into the WSL filesystem and CuPy loads the wrong
    CUDA. Also pin a consistent CUDA-12 runtime wheel set (`nvidia-*-cu12`) when the system
    toolkit is an older CUDA, or cuFFT/nvrtc break. (→ [24](24-voicemeeter-passthrough.md))
52. **Never do heavy DSP inside a real-time audio callback — render on a worker thread.** The
    8-ch BRIR convolution (tens of ms, GPU sync) run inside the WASAPI capture callback held the
    GIL and blocked, starving *both* audio callbacks: effective throughput collapsed to ~31 kHz
    and audio dropped constantly — while PortAudio's own underflow flag stayed silent, so the
    bug is invisible unless you measure effective rates. The callback must only enqueue (capture)
    or drain (output) and return in microseconds; a dedicated worker thread does the render and
    the FIFO cushion absorbs its jitter. Proven by A/B: a trivial render over the identical I/O
    bridge sustained a flawless 48 kHz, 0 underruns. (→ [24](24-voicemeeter-passthrough.md))
53. **Diagnose hardware audio by driving a KNOWN signal through the real path and measuring —
    not by ear, and not by trusting headless tests.** Channel order, sample rates, real-time
    timing and spectral coloration are properties of the device path, invisible to unit tests.
    Feed a distinct tone per channel to recover the true capture order before assuming it matches
    your code; feed broadband per channel and compare input/output spectra to find lost bass or
    coloration. This found that the capture order *did* match, every channel renders, but the
    baked BRIRs roll off sub-bass ~−20 dB — so the **LFE** (pure sub-bass) disappears: route a
    non-directional LFE flat to both ears, don't convolve it through a bass-dead directional
    HRIR. Toolkit: `spikes/b4_1_loopback_diag.py`; method: the `audio-loopback-diagnostics`
    skill. (→ [24](24-voicemeeter-passthrough.md))
54. **A headphone comp + subjective EQ is a global output-bus stage, not per-BRIR — so port it
    once, after the downmix.** EqualizerAPO applies the owner's comp (per-ear FIR + biquads) and
    subjective EQ to the final L/R mix, *after* the per-speaker convolution and downmix. Because
    the renderer's neutral output already equals that downmix, the only thing a bare bake is
    missing is this one chain; reproducing it as a stereo output-bus stage (gain + comp FIR +
    biquads per ear) leaves the 16 BRIRs — and the world-lock steering — untouched, and is
    retunable live. Two leverage points: (a) you don't need an acoustic loopback to find the
    correction — both paths are digital binaural to the same DAC, so the DAC cancels and the
    chain's own frequency response *is* "what differs", computable from the config; (b) isolate
    the global chain from a config mechanically by accumulating ops on buses L/R and **clearing a
    bus on any `Copy` that reassigns it** (the downmix the renderer itself makes), so only
    post-downmix ops survive. Caveat: the comp/EQ's big bass shelves exist to restore the
    sub-bass the LFE BRIR strips, so the output EQ must pair with `--lfe-mode brir`, not the B4.3
    flat LFE, or the bass doubles. Proven by reproducing the sibling's full-chain reconstruction
    to 1.5e-14. (→ [25](25-output-bus-comp-eq.md))
54b. **When "it reacts too slowly" but the on-screen tracker is instant, the lag is downstream
    in the output buffer, not the sensor.** The GUI draws the filtered pose, so a fresh GUI
    proves camera+detect+filter are fast; the only remaining delay is the audio path. Here it
    was the output FIFO cushion (a head turn is heard ~cushion late, because already-rendered
    audio drains first) plus a `max_backlog` that let drift balloon it — fixed by lowering the
    cushion, capping the backlog near it (dropping only *excess future* audio never underruns),
    and asking the device for low latency. Diagnose latency by *which stage still looks fresh*,
    not by guessing. (→ [26](26-reaction-latency-and-cpu-headroom.md))
55. **Profile before porting: a GPU (or a rewrite) may be masking interpreter overhead, not a
    real compute wall.** The 8ch×500 ms BRIR convolution that "needed" the GPU (journal 24,
    ~0.97× RT) is actually **1.6–4.3× realtime on one CPU core** as raw partitioned convolution;
    the full pipeline's 0.93× is Python per-block orchestration (voice bookkeeping, crossfade
    einsum, allocations), not FFTs. And naive per-block Python threading is *slower* (0.2× RT) —
    future-dispatch dwarfs ~180 µs of work. So the right GPU-drop path is a **targeted native
    (Rust/PyO3) render kernel** for the hot loop only — never a full rewrite that would also drag
    in the camera stack (MediaPipe/OpenCV) that was never the bottleneck. (→ [26](26-reaction-latency-and-cpu-headroom.md))
56. **A narrow native kernel for just the hot loop recovers the whole GPU — on CPU.** Porting
    only the batched partitioned convolution to Rust (PyO3, rustfft + rayon, allocation-free,
    multithreaded over the 8 buses) — keeping the routing/crossfade/LFE and all tracking in
    tested Python — hit **~5× real time at block 256** (pure-Python was 0.4×) and **bit-exact
    parity (~1e-16)** with the Python renderer. Two enablers made the port cheap and safe: the
    bus reformulation already isolated the heavy piece as one fixed-filter batch (so the native
    code is a pure DSP leaf with no app logic), and it inherited the existing `BusMixer == voice
    pool` proof, so the kernel only had to match `PartitionedConvolver` bin-for-bin (divide the
    inverse real FFT by `nfft`, zero the DC/Nyquist imaginary bins to match numpy). Keep it an
    **optional accelerator** (separate maturin crate, `importorskip` on CI) so the Python wheel
    and CI stay toolchain-free. (→ [27](27-rust-render-kernel.md))
57. **A native extension can be shipped cross-platform from one Linux box, no paid CI — build
    abi3 and cross-compile.** `abi3-py310` makes one stable-ABI wheel per platform cover CPython
    3.10+ (incl. the newest), so you never rebuild per Python minor and "use a recent Python" is
    free. Windows wheels cross-compile from WSL with `cargo-xwin` (+ `pyo3/generate-import-lib`
    to synthesise `python3.lib`, no Windows/VS); the gotchas are a rustc new enough for
    cargo-xwin, the LLVM CLI tools (`clang`/`lld`/`llvm`, plus a `clang-cl` symlink Ubuntu omits),
    and a **native-ext4 `CARGO_TARGET_DIR`** (`llvm-dlltool` EACCESes on `/mnt/*` drive mounts).
    **macOS is the exception** — no clean cross-build from Linux (Apple SDK + realistically a
    Mac; you can't emulate another OS, only another CPU arch for Linux) — so build on the Mac or
    lean on the pure-Python fallback that keeps the app running everywhere. (→ [28](28-cross-platform-wheels.md))
58. **For world-lock, the latency that matters is the *pose age at the moment the sound is
    heard*, and it is a distribution, not a sum of component maxima.** Four delays feed it —
    image (exposure/read-out/USB/buffering, *not* `1/fps`), compute, audio render (block +
    device buffer, *not* FIR length), and the residual `ω·Δt` during motion — but they don't
    simply add: capture and DSP pipeline, and a pose's age depends on the phase between frame
    arrivals and audio blocks. So measure it as **p50/p95/p99 + extremes** (a rare 100 ms spike
    hurts a world-locked scene far more than a higher mean), not as one averaged number.
    (→ [36](36-latency-budget-and-prediction.md))
59. **Two *different* perceptual thresholds bound the budget — don't conflate them.** (a)
    Detecting the *lag itself* (the scene swims): ~50–75 ms just-noticeable total system latency
    in dynamic binaural synthesis (Lindau 2009, lowest ~52 ms) → keep pose age under ~50 ms, ~30
    is headroom. (b) Resolving the error *as an angle*: minimum audible *movement* angle is ~8° at
    90°/s (Perrott & Musicant 1977), so ~3° of latency-lag during a turn is sub-threshold *during
    motion* — but the finer ~1° static MAA applies the instant the head *stops*. Design
    consequence: cut effective lag during motion yet **settle cleanly at the stop** — exactly what
    a bounded-horizon orientation predictor (const-ω quaternion extrapolation + α-β, Δt→0 on
    deceleration) targets, re-evaluated per audio block so reactivity decouples from the camera
    fps. (These numbers are literature recall, not KB- or self-verified; B8.5 measures ours.)
    (→ [36](36-latency-budget-and-prediction.md))
60. **Measure the pose age before buying a camera — period ≠ pipeline latency.** Detect is ~3 ms
    and resolution-independent; the camera (capture, not USB) is the bottleneck, and the C270's
    earlier 15 fps was low-light **auto-exposure**, not the sensor (journal 31). So first fix
    lighting / set manual exposure + fixed focus and decouple acquisition with **drop-latest**
    (newest frame, discard the stale queue); only if a B8.5 measurement shows the *sensor*
    dominates is a 60 fps **uncompressed** webcam (manual exposure, no autofocus) worth it — a
    nominal 90–120 fps camera only shaves the frame *period* (16.7→11.1 ms) and says nothing about
    the full capture→application delay. A second camera is for angular coverage/occlusion (async
    timestamp fusion first), not speed. (→ [36](36-latency-budget-and-prediction.md))
61. **The bus-mix reformulation generalizes from "one bus per speaker" to "one bus per direction".**
    The world-lock renderer cross-fades its inputs into *fixed-filter buses* and batch-convolves
    them; `nearest` needs only 8 (one per speaker). A *synthesis* method has no single nearest BRIR
    per channel, so it fans the 8 inputs into a **dense N-bus grid** (one synthesized BRIR every
    `grid_step`°) — same machinery, same **1e-9 parity** against the per-direction voice pool, same
    native kernel (it already takes a bus count; no Rust change). But N buses is a *different*
    performance regime, not a tweak: keep `nearest`'s fast 8-bus branch separate, and expect a
    synthesis 7.1 to want `--rust` + a bigger block (pure-Python ~1.35× RT at block 1024/15°,
    sub-RT smaller). (→ [37](37-shared-interpolation-method.md))
62. **A hidden per-mode setting confuses more than it helps — share it and show it.** The owner
    couldn't reason about a "method" that silently meant one thing for the test probe and another
    (`nearest`) for the music they actually heard, and was invisible at rest. Collapsing it to **one
    shared knob that governs the real output and is always on screen** (a title-bar chip) removed the
    confusion outright — worth more than the extra render cost. When a control's *lifecycle* is
    unclear to the user, unify its scope before optimizing it. (→ [37](37-shared-interpolation-method.md))
63. **A steered voice pool's real-time cost is set by *motion*, not by the method.** Each grid
    direction is a persistent convolver voice that rings its BRIR's full tail (~0.5 s) after the
    source leaves it. A *stationary* source is ~1 voice (cheap, any method); a source that **sweeps**
    a fine grid holds a full-length convolution alive for every point it passes — so the voice count,
    and the dropouts, scale with grid density × sweep speed, not with the interpolation maths
    (synthesis is a one-off build cost). timealign @10° hit 13 concurrent voices / 0.81× RT while
    `nearest` (8 coarse dirs) stayed ~4 and fine. Fix: **cap the retired-voice tail** — ring a
    left-behind voice for a bounded window with an output fade-out, then drop it; keep the *active*
    voice's full tail so a still source is unchanged. Capping ~0.5 s → 150 ms took it to 5 voices /
    1.95× RT, click-free. When profiling a voice/partial-mixing engine, benchmark the **moving** case;
    the static one hides the cost. (→ [37](37-shared-interpolation-method.md))
64. **A health panel must show the *configuration* that produced the numbers, not only the numbers.**
    The owner saw 100%+ render load but couldn't connect it to a fix because the panel never showed
    the engine/method/block it was running — "rust? block 1024?" was a guess (it was block 256). Read
    the config **off the live renderer object** (not remembered launcher args, which drift) and print
    it: engine · method · grid · block (samples *and* ms). And when several sources share one budget,
    **attribute cost per source** — one combined load hid that the test source (droppable with one
    key) was what tipped it over; splitting it into `7.1 AA% + test BB%` made the fix obvious. Corollary:
    shipping a default that *raises the baseline cost of the daily path* (B4.4's shared method on the
    full 7.1) must update the launcher/runbook in the same change — an old launcher had pinned a small
    block chosen when that path was cheap. (→ [38](38-stats-render-config.md))
65. **Measure the latency you care about as a distribution before trying to cut it.** The felt
    quantity is *pose age at the ear* — how old the head pose is when the sound carrying it is heard.
    We had only an estimate (`1.5 × frame interval` + buffer sum), which hid both which stage
    dominates and the p99 spikes that break world-lock worse than a bad mean does. The cheap, no-
    hardware measurement: stamp each pose (`HeadPose.t`), and at the instant its block is queued for
    playout subtract `now − pose_t` and add the audio still queued ahead + the device buffer; keep
    **p50/p95/p99 + extremes** over a rolling window, not a mean. Bonus: the pose-timestamp seam you
    add to *measure* is the exact seam the *fix* (B8.6 forward prediction) plugs into — so measuring
    first is free groundwork, not a detour. The one part software can't see (camera exposure before
    the timestamp, ~1 frame) stays an explicit labelled estimate rather than a silent omission.
    (→ [40](40-pose-age-measurement.md))
66. **Time-aligning then blending two broadband directs inflates the bass; fix loudness with a
    both-ears-equal real gain.** `timealign` aligns the two neighbours' direct arrivals and sums
    them with linear weights — but once aligned, their long wavelengths are correlated and add
    **constructively**, so a synthesized in-between BRIR gains ~2–3 dB of bass over the measured
    endpoints. The result is a level *ripple* that dips at the measured nodes, which the listener
    hears as the surround going bass-light whenever the head faces a speaker and swelling when it
    turns off-axis (the tail blend already dodged this with √-power weights; the direct did not).
    The safe correction: rescale the synth's **smoothed combined (L²+R²) envelope** to the
    weight-interpolated **measured** endpoints, applying the **same real per-frequency gain to both
    ears** — real + shared ⇒ the inter-ear ratio (ILD) and phase (ITD) are untouched, so loudness is
    fixed without disturbing localisation; interpolate toward the *measured* endpoints so nodes stay
    exact and the pan is constant-loudness. (→ [41](41-timealign-loudness-match.md))
67. **For correlated/stereo content the effective binaural response is just the sum of the active
    channels' BRIRs — so you can diagnose spectral complaints by exact computation, no playback.**
    Summing the channel BRIRs at their head-relative directions and taking the magnitude separated
    "interpolation artifact" from "HRIR balance" in a single offline measurement (nearest flat,
    timealign rippled), and it works on the owner's real BRIR straight off disk. Reach for the exact
    sum before reaching for a mic or a listening test. (→ [41](41-timealign-loudness-match.md))
68. **When a knob "does nothing", stop guessing and measure the latency by *stage*.** Halving the
    block barely moved the total (p50 138→143) because the total was ~⅔ a fixed **device buffer** the
    summed number hid. Splitting pose-age-at-ear into **stale** (pose age) / **queue** (FIFO) /
    **device** (output buffer) made the knob→stage map obvious: block→queue, camera→stale, WASAPI
    mode→device — and revealed the ~90–100 ms WASAPI shared-mode buffer as the thing to chase. A
    single summed latency is a trap for tuning.
69. **A synthetic pose + synthetic tone turns an interactive, device-bound tuning loop into an
    unattended sweep.** No human, no camera, no music, deterministic; a real-time-clocked consumer
    simulates the device so it runs headless anywhere, and the one thing the sim can't model (the real
    device buffer) is exactly what a host `device`-mode run adds back — the two together fully
    attribute the latency. Always have the harness **report distributions + dropouts and verify the
    tone passes**, so it can't "win" by silently dropping audio. (→ [42](42-latency-benchmark-harness.md))
70. **On Windows the host API a device opens under is a first-class latency parameter — prefer
    WASAPI and show which you got.** The FiiO is exposed under MME / DirectSound / WASAPI / WDM-KS;
    "first substring match wins" resolved `"FiiO"` to **MME**, whose shared buffer is ~90–100 ms —
    the exact fixed device term the component split (journal 42) said dominated. Same DAC, ~80–100 ms
    difference, pure configuration not code. Audio device-name resolution must prefer WASAPI (then
    WDM-KS, DirectSound, MME) and log the chosen API + buffer, so this can't hide again. (The
    measurement harness is what made a config bug look like a config bug instead of a DSP mystery.)
    (→ [43](43-output-on-mme-prefer-wasapi.md))
