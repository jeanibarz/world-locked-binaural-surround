# AGENTS.md — contract for the autonomous build chain

This repository is built **block by block** by a self-continuing Kookr agent
chain. Each task does **one small, independent block**, records its progress in
durable files, and leaves the repo green. This file is the contract every task
reads at the start of its run. It must stay self-sufficient: a task starts
**cold**, with no memory of previous tasks — everything it needs is here, in
[`docs/backlog.md`](docs/backlog.md), and in
[`docs/research-journal/`](docs/research-journal/README.md).

## What this project is

A real-time, head-tracked, world-locked binaural renderer for Windows. Read
[`README.md`](README.md) for the idea and [`docs/requirements.md`](docs/requirements.md)
for the full requirements and architecture before doing anything else.

## The one-block loop (how each task works)

1. **Read durable state, not memory.** Open `docs/backlog.md` (the ordered block
   list), the latest `docs/research-journal/` entries, and `git log`. Decide the
   single next eligible block from what is *recorded done*, never from assumption.
2. **Pick exactly one block.** The smallest next unit that moves a milestone.
   Prefer unblocking/foundational work. If a block is bigger than one sitting,
   split it and do only the first slice (and record the split in the backlog).
3. **Do that one block end to end**, in a fresh git worktree (see below). Write
   or update tests where code is involved. Keep the repo runnable and green.
4. **Record the outcome** in the same run:
   - Add/append a **research-journal entry** (hypothesis/method/result/decision,
     negative results included) and update its index + `LEARNINGS.md`.
   - Tick/advance the **backlog**: mark the block done, add any follow-ups or
     newly-discovered blocks.
   - Commit with a Conventional-Commits message.
5. **Deliver** per the delivery policy below, then **hand off** (continue the
   chain or stop) per the self-continuation policy below.

Do **one** block per task. Do not batch blocks because setup is warm.

## Durable state = the three files

- **`docs/backlog.md`** — the ordered, checkbox list of work blocks grouped by
  milestone. This is the queue. The "next eligible block" is the first unchecked
  block whose dependencies are checked. Keep it honest and current.
- **`docs/research-journal/`** — the linear lab notebook: one entry per block or
  decision, an index (`README.md`), and `LEARNINGS.md`. This is *why* and *what
  changed*, written for a cold reader. Mirror the maintenance protocol used by
  the sibling project `binaural-localization`.
- **`git` history** — commits and (if a remote exists) PRs are the completion
  evidence. Prefer "a merged change exists" over "I remember doing it".

## Git & delivery

- **Never commit to `main` in the primary checkout.** For every tracked-file
  change, create a fresh worktree:
  `git worktree add ../binaural-headtracker-<slug> -b <branch> main`
  (or `origin/main` if a remote exists). Edit, commit, and push from there.
- Conventional Commits; small, atomic, green commits.
- If a GitHub remote is configured: open a PR per block and, for the owner's own
  repo, **merge after local verification** (tests/build green). Record the PR in
  the journal. If no remote: committing to the feature branch and fast-forwarding
  `main` locally after verification is the delivery step.
- Do **not** merge red, broken, or unverified work. Record a blocker instead.
- Keep every added dependency licence-compatible with a future donationware
  release (prefer MIT/BSD/Apache; flag GPL). Record each dependency + licence in
  the journal.

## Self-continuation (24/7, resilient to kills)

This chain is designed to run unattended and survive a task being killed
mid-block, because **state lives in files, not memory**. Each task:

- Determines the next block **only** from `docs/backlog.md` + journal + git.
  A killed task leaves no half-written durable state if you commit atomically at
  the end of the block (work-in-progress stays on an un-pushed worktree branch
  and is simply re-derived).
- When the block is done and recorded, **hands off**: spawn the next task in the
  chain (same contract, a content-distinct cursor naming the next block), or stop
  if the backlog has no eligible block. Follow the
  `kookr-toolkit:self-continuation-task` skill for the exact handoff + slot-release
  mechanics.
- **Context-pressure guard.** If the working context grows large (≈ the
  configured threshold, e.g. ~500k tokens) *before* a block is finished:
  stop expanding scope, write a short journal note capturing exactly where the
  block stands and the precise next step, commit any safely-committable progress,
  and hand off a successor whose cursor points at *finishing this same block*.
  Never carry unfinished reasoning across the boundary in memory — put it in the
  journal.

## Runtime environment (where spikes actually run)

The dev box is **WSL2 (Linux)**, but it sits on the owner's **Windows** host and
reaches it through **WSL interop** — so Windows-only spikes (audio/WASAPI,
camera) are runnable **from here**, not blocked:

- `py.exe -3.12` launches a native **Windows Python** that has the real audio
  stack (`sounddevice`/PortAudio → WASAPI) and the webcam. Run a repo script by
  its Windows path: `WINPATH=$(wslpath -w <script>); py.exe -3.12 "$WINPATH"`.
- WSL itself exposes only WSLg PulseAudio (no WASAPI, no soundcard), which is
  **not** representative of the Windows low-latency path — never quote WSL audio
  numbers. Keep portable logic in the package (unit-tested on WSL/CI without any
  device), and put hardware-touching code in `spikes/`.
- Do **not** record an audio/camera block as "blocked on WSL" — drive the
  Windows Python. (See journal 02.)

### Running the host GUI apps (camera + audio + a window) — ON WINDOWS, not WSL

The interactive apps in `apps/` (`calibration_gui.py`, `live_tracker_audio.py`) and
the vision/audio spikes need a **real webcam, a real audio output, and a display** —
they **must run on the Windows host**, never under WSL (no `/dev/video*`, no WASAPI,
and the package's `cv2`/`mediapipe`/`sounddevice` aren't even installed on the WSL
side). Drive them through interop with the **host** Python, which *does* have those
libs (`py.exe -3.12` → `C:\Users\<user>\AppData\Local\Programs\Python\Python312`). The
recipe, with the two gotchas that will otherwise waste a launch:

1. **Inject the package via `sys.path`, not `PYTHONPATH`.** The host Python does not
   have `binaural_headtracker` installed, and on this box `PYTHONPATH` is overridden by
   the environment, so exporting it does nothing. Instead run a tiny **host-local**
   launcher that prepends the worktree's `src` and `apps` to `sys.path` and calls the
   app's `main()`. A `\\wsl.localhost\<distro>\home\...\src` **UNC path imports fine**
   from Windows Python — that is the supported way to run the WSL worktree's code on the
   host without installing anything:
   ```python
   # C:\...\launch.py
   import sys
   sys.path[:0] = [r'\\wsl.localhost\Ubuntu-22.04\home\jean\git\<worktree>\apps',
                   r'\\wsl.localhost\Ubuntu-22.04\home\jean\git\<worktree>\src']
   import live_tracker_audio as app  # or calibration_gui
   raise SystemExit(app.main())
   ```
   Then `py.exe -3.12 C:\...\launch.py <the app's --flags>`. (`wslpath -w <path>` gives
   the UNC path for a worktree file.) Launch it with the Bash tool's
   **`run_in_background`** — it's a long-lived GUI; the completion notice is the user
   quitting (or a crash to read).
2. **MediaPipe can't open a UNC model path.** `cv2`/`sounddevice`/imports take UNC, but
   the FaceLandmarker C++ loader throws `FileNotFoundError` on `\\wsl.localhost\...` —
   **copy the `.task` to a local `C:` path** and pass that as `--model`. (Baked BRIR dirs
   and source WAVs are already real `C:` paths, so they're fine.)

Host assets (owner's, kept out of git): the FaceLandmarker model
`spikes/models/face_landmarker.task` (download per `spikes/b0_4_head_tracker.py`; also
keep a local-`C:` copy for MediaPipe), the baked BRIR dir (`docs/baked-brir-setup.md`),
and dry source WAVs (e.g. `caisse_claire_*`). A WSLg Tk window can **preview** a dialog
from WSL, but the camera + audio always run on the host. (See journal 02; B6.2.)

**The owner's host install location is `C:\Users\ibarz\BinauralHeadtracker\`** (per-user;
Program Files needs admin we don't have). It holds the launchers (`launch_live.py`,
`launch_passthrough.py` — each prepends the worktree's `apps`+`src` to `sys.path`, trying
the active dev worktree first then the `main` checkout), the double-click `.cmd` wrappers
(they use `%~dp0`, so they keep working wherever the folder is), the local-`C:` model copy,
and `dry_snare.wav`. Put new host launchers/assets here, not in Downloads.

**GPU compute (CuPy on the RTX 3090).** The CPU can't render the 8-channel 500 ms-BRIR
passthrough in real time (~0.97× RT); the GPU does (batched partitioned convolution ≈6×
RT at block 256, full BRIR). Host Python has `cupy-cuda12x` **plus the matching CUDA 12
runtime wheels** — `nvidia-{cuda-runtime,cufft,cublas,cusparse,cusolver,curand,
cuda-nvrtc,nvjitlink}-cu12` — because the system only has CUDA 11.x toolkits and CuPy 13's
pathfinder needs a consistent CUDA-12 set in site-packages (installing only some leaves
cuFFT/nvrtc broken). Two gotchas when launching the host Python **from WSL**: (1) run it
with the cwd on a **real `C:` drive**, never the WSL worktree — CuPy's `_get_cuda_path`
falls back to `if os.path.exists('/usr/local/cuda')`, which resolves into the WSL mount
when the process cwd is a `\\wsl.localhost\…` path and makes it load the wrong CUDA; (2)
the owner's `.cmd` runs on Windows so neither applies there. (See journal 24; B4.1/B4.2.)

**Native render kernel (Rust/PyO3) — the GPU-free fast path (B8.2).** `rust/` is a separate
maturin crate `binaural_kernel` (the batched partitioned bus convolution in Rust), an
**optional accelerator** — not part of the Python wheel, never required on CI (tests
`pytest.importorskip("binaural_kernel")`). It reaches ~5× RT on CPU at block 256 (bit-exact vs
the Python renderer), so the GPU is now optional. Build it into the venv with a Rust toolchain:
`VIRTUAL_ENV=.venv maturin develop --release -m rust/Cargo.toml` (or from `rust/`). The apps
select it with `--rust` (takes precedence over `--gpu`; falls back to pure-Python CPU if
unbuilt). `rust/target/` is gitignored; `rust/Cargo.lock` is committed. (See journal 27.)

**Distributing the kernel cross-platform (B8.3).** Built **abi3** (`abi3-py310`), so one wheel
per platform runs on CPython 3.10+ (including the newest — the host Python version is gated by
mediapipe/opencv wheels, not the kernel). `rust/build-wheels.sh [linux|windows|all]` builds
wheels into `dist/` (gitignored) with **no CI**: Linux native, and **Windows cross-compiled from
WSL** via `cargo-xwin` + `pyo3/generate-import-lib` (prereqs in the script header: the
windows-msvc target, `cargo install cargo-xwin`, `apt install clang lld llvm`, a `clang-cl`
symlink, and a native-ext4 `CARGO_TARGET_DIR` — `llvm-dlltool` EACCESes on `/mnt/*`). **macOS is
not cross-buildable from Linux** (Apple SDK + a Mac): the Mac colleague runs `maturin build`
locally or uses the pure-Python fallback. Install a built wheel with `pip install <wheel>` into
the Python that runs the app. (See journal 28.)

## Working discipline

- **Prototype in Python first** for fast iteration, but the language is **not
  locked**: a block may prototype or benchmark a hot path in **C/C++/Rust** as
  soon as performance is the deciding question — don't defer all native work to
  M8 (see requirements § 5). Record the language choice and its licence in the
  journal; keep config/HRIR/world-model shared across languages.
- One variable at a time for any comparison; state the hypothesis before the
  result; record negative results. (Mirrors the sibling project's method.)
- Keep large binaries (SOFA, wav, models) **out of git** (see `.gitignore`);
  document how to fetch/rebuild them.
- When unsure between a small implementation and a larger design question, if the
  block is implementation, implement; if it is a genuine architecture fork,
  record an RFC-style journal entry proposing a direction and proceed with the
  best-justified option rather than stalling.

## Analysis depth — extract the maximum (NON-NEGOTIABLE)

One experiment, one average, and a couple of aggregate stats is **not** an
analysis — it is the *start* of one. Shallow "ran it, averaged it, done" is the
single biggest failure mode here; catch yourself in it. Before concluding
**anything**, at high effort:

- **Use every tool and knowledge source you have — all of them.** You are not
  limited to a Python REPL. Reach for: the sibling's **synthetic-listener judge**
  (`binaural_localization.second_listener`) and its **pinna-notch toolkit**
  (`binaural_localization.notch`) and DTF/interpolation code; the domain
  **knowledge bases** via `kb search` / the `kb-scout` agent (`hrtf-individualization`,
  `auditory-localization-models`, `sound-source-localization`,
  `experiment-methodology`, …); the research literature; subagents for parallel
  analysis. Leaving a relevant instrument unused is a defect, not a shortcut.
- **Analyse from every angle, never one.** Per-item *and* aggregate; over time
  *and* over frequency; per-channel *and* per-direction; distributions, outliers
  and trends — not just the mean. The mean hides exactly the structure that
  explains the phenomenon (an L/R asymmetry, a per-direction dip, a reverb-tail
  wander all vanish into an average).
- **When you have multiple evaluators/judges, run MANY evaluations and
  triangulate.** Cross-check every claim by an independent method. A conclusion
  only one metric supports is a *hypothesis*, not a finding; several cheap probes
  that agree beat one headline number.
- **Let the ear (and the judge) arbitrate, then find the metric that agrees.**
  If a measurement disagrees with perception, the measurement is the wrong proxy
  — keep digging for the right one (see LEARNINGS L33, the DRR correction).
- Conclude only when the evidence is **exhausted and converges**, and record what
  you **ruled out**, not just what you found.

This is how the research moves toward the correct direction. Depth first.

## The sibling project

`../binaural-localization` is the research project building the **synthetic-listener
judge** and HRIR/EQ findings this project reuses for calibration (FR-10/11). Read
its `docs/research-journal/` when working calibration blocks; do not duplicate its
work — depend on it. It also carries reusable analysis instruments to bring to bear
(per *Analysis depth* above): `second_listener` (localization judge), `notch`
(pinna-notch metrics), `dtf`, and the `eqapo` engine.

### Where to find judge models (the registry)

Trained localization judges are published to a **shared, persistent, non-versioned
registry** — default **`/mnt/d/binaural-models/registry/`** (a 4 TB drive outside
every worktree, so it survives branch switches), override with
**`$JUDGE_MODEL_REGISTRY`** (binaural-localization honours the same var). **Never
guess which `saved_model/*.keras` to use** — resolve the best validated judge with
`binaural_headtracker.judge_registry.best_judge()` (pure-python; returns the model
path + metadata/metrics). Current best: **`pooled5_distribution_v1`** (pooled 5-rig,
cross-rig QE 9.0%, in-domain 0.44%). The keras model loads in the *sibling's* env;
this repo only resolves the path. See `judge_registry` for the contract.

### Reading the judge output (observability methodology)

When scoring localization with the judge, read its **full 256-bin distribution**, not
a decoded point estimate — a bare angular error hides *how* it is wrong. Reuse the
sibling's `explain_localization.py` / `diagnose_distribution.py` to decompose each
direction's error onto the two orthogonal cue axes and report, per direction:

- **lateral (ITD/ILD, binaural):** expected lateral angle + error + spread;
- **polar / front-back (monaural spectral/pinna):** `geometry.distribution_front_prob`
  (≈0.5 = genuine ambiguity, not a silent collapse) + a **bimodality / front-back
  confusion** flag;
- **distribution concentration/entropy** (sharp single lobe = confident; diffuse or
  bimodal = confused), so you can tell *confident-but-flipped* from *genuinely
  ambiguous*; and, where useful, the measured cues (ITD, ILD, pinna notch).

This tells you *which cue* fails and the judge's confidence — the input to any
"adjust to improve" loop. **Validity bound (sibling entries 20/22): the judge is
trustworthy on the LATERAL axis but LOW-CONFIDENCE on polar/front-back for
owner-domain (reverberant/EQ'd) input.** So trust lateral/ITD statements, treat
front/back as directional-not-definitive, and **confirm front/back by ear** on a
listening test before concluding.
