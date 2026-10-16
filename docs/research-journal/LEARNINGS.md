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
