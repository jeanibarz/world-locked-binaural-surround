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

### Elevated host control — the SSH admin channel (for driver/VM work)

WSL interop runs **non-elevated** (it inherits the WSL process token; it cannot be elevated
without relaunching the session). Track B (the custom audio driver, B8.7b) needs **admin**:
installing the WDK, creating/managing a **Hyper-V VM**, and loading/test-signing drivers. So a
one-time elevated setup established a **key-only SSH channel into the host that carries a full
admin token** — this is how an unprivileged WSL session gets elevated host control without a
restart. (Owner granted "max autonomy", journal 54.)

- **Connect:** `ssh -i ~/.ssh/kookr_host_admin ibarz@$(ip route | awk '/default/{print $3}')`
  — the host is the WSL **default-gateway** IP (≈`172.23.208.1`, but **rediscover it** each
  session; it changes across WSL restarts). User **`ibarz`** (a local admin). The session lands
  **elevated** (`IsInRole(Administrator)` → True, because `LocalAccountTokenFilterPolicy=1`).
- **Remote shell is `cmd.exe`** by default; wrap PowerShell as
  `ssh … ibarz@HOST "powershell -NoProfile -Command \"<expr>\""`.
- **Keys/config:** private key `~/.ssh/kookr_host_admin` (WSL only, **never committed**); the
  public half is authorized in `C:\ProgramData\ssh\administrators_authorized_keys`. sshd is
  **key-only** (passwords off) and firewalled to the **WSL subnet** (`172.16.0.0/12`).
- **Setup / revert:** `spikes/host_admin_sshd_setup.ps1` (run once, elevated) /
  `spikes/host_admin_sshd_revert.ps1` (removes the channel + the policy). `sshd` can be stopped
  anytime to cut access instantly.

**Safety contract for this channel (NON-NEGOTIABLE — it holds real admin):**
- **Kernel drivers load ONLY inside the Hyper-V VM, never on the host.** The host keeps Secure
  Boot on and test-signing off, so host audio and anti-cheat games are never disturbed.
- **Take a Hyper-V checkpoint before every driver install/load** in the VM (one-command rollback
  of a BSOD/bad driver).
- **Never touch host boot config** (`bcdedit`, test-signing, Secure Boot) or the host's default
  audio device / VoiceMeeter routing.
- **Confirm with the owner before anything irreversible or outward-facing** on the host (deleting
  a VM, uninstalling tools, network-exposing changes). Installs, VM creation, checkpoints, and
  in-VM work are fine to do autonomously.

### Running the host GUI apps (camera + audio + a window) — ON WINDOWS, not WSL

The host apps — the world-lock passthrough engine (daily driver + the cockpit UI) and
`calibration_gui.py` — and the vision/audio spikes need a **real webcam, a real audio
output, and a display** — they **must run on the Windows host**, never under WSL (no
`/dev/video*`, no WASAPI, and the package's `cv2`/`mediapipe`/`sounddevice` aren't even
installed on the WSL side). There is **one** host entrypoint, `apps/launch.py`, with a
**mode** as its first argument (`listen` | `test` | `calibrate` | `latency`); the host's
single `run.cmd` (tracked template in `host/`) forwards to it. Drive it through interop
with the **host** Python, which *does* have those libs (`py.exe -3.12` →
`C:\Users\<user>\AppData\Local\Programs\Python\Python312`). The recipe, with the two
gotchas that will otherwise waste a launch:

1. **Inject the package via `sys.path`, not `PYTHONPATH`.** The host Python does not
   have `binaural_headtracker` installed, and on this box `PYTHONPATH` is overridden by
   the environment, so exporting it does nothing. Instead call `apps/launch.py` over a
   `\\wsl.localhost\<distro>\home\...` **UNC path** — it prepends its own repo's `src`
   and `apps` to `sys.path` from `__file__` and dispatches to the mode's `main()`. UNC
   imports work fine from Windows Python — the supported way to run the WSL checkout's
   code on the host without installing anything:
   ```bat
   py.exe -3.12 "\\wsl.localhost\Ubuntu-22.04\home\jean\git\binaural-headtracker\apps\launch.py" listen <--flags>
   ```
   (`wslpath -w <path>` gives the UNC path for a worktree file; `host/run.cmd` wraps this
   with the owner's device flags and the exit-97 reload loop.) Launch it with the Bash
   tool's **`run_in_background`** — it's a long-lived GUI; the completion notice is the
   user quitting (or a crash to read).
2. **MediaPipe can't open a UNC model path.** `cv2`/`sounddevice`/imports take UNC, but
   the FaceLandmarker C++ loader throws `FileNotFoundError` on `\\wsl.localhost\...` —
   **copy the `.task` to a local `C:` path** and pass that as `--model`. (Baked BRIR dirs
   and source WAVs are already real `C:` paths, so they're fine.)

Host assets (owner's, kept out of git): the FaceLandmarker model
`spikes/models/face_landmarker.task` (download per `spikes/b0_4_head_tracker.py`; also
keep a local-`C:` copy for MediaPipe), the baked BRIR dir (`docs/baked-brir-setup.md`),
and dry source WAVs (e.g. `caisse_claire_*`). A WSLg Tk window can **preview** a dialog
from WSL, but the camera + audio always run on the host. (See journal 02; B6.2.)

**Anything visual (alignment, symmetry, text width, clipping, DPI) is checked by screenshot +
pixel measurement, never by reasoning about the math or by an isolated probe** — load the
`host-gui-visual-check` skill (`spikes/host_window_shot.ps1` + `spikes/host_shot_measure.py`).

### Measurements & latency experiments — headless, run them here too

**"Needs host hardware" in the backlog/journals (e.g. B8.5, the latency split, B8.8) means
*run it on the host via interop* — NOT "blocked / deferred".** The measurement harnesses are
**headless** (no window, no dialog), so they're easier than the GUI apps above — don't write
"needs host hardware, deferred" without first running it from here:

- **Device buffer + split, unattended:** `apps/latency_sweep.py` / `latency_bench.py
  --output device --audio-device FiiO` plays a synthetic tone to the real endpoint and reports
  the true device buffer. (`run.cmd latency` on the host writes `latency_sweep_results.json`.)
- **The live `queue`/`stale` audio-pipeline terms do NOT need a face.** The camera only sets a
  pose *value + timestamp*; it does not touch FIFO timing. To measure them unattended, replicate
  the passthrough's loopback-capture → burst-drain worker → output FIFO with a **synthetic
  `pose_t`** (set at ~30 Hz) — real VoiceMeeter loopback + real FiiO out, no camera, no window.
- The only things you genuinely can't do from here are a **human moving their head** and judging
  audio **by ear** (the motion→sound ground-truth, B8.5 §c). Everything else is runnable now.

**The owner's host install location is `C:\Users\ibarz\BinauralHeadtracker\`** (per-user;
Program Files needs admin we don't have). It holds the **single** `run.cmd` (the tracked
template is `host/run.cmd`; it uses `%~dp0` so it keeps working wherever the folder is, and
calls `apps/launch.py` in the `main` checkout over the UNC path), the local-`C:` model copy,
`dry_snare.wav`, and the `hrir_sets\` candidates. Put new host assets here, not in Downloads.
Deploy/update the launcher by copying `host/run.cmd` over (see `host/README.md`); keep it a
single start script — add a *mode* to `apps/launch.py`, don't spawn another `run_*.cmd`.

**GPU compute (CuPy on the RTX 3090) — now OPTIONAL.** The native Rust kernel (B8.2, the
`--rust` path) renders the 8-channel 500 ms-BRIR passthrough in real time on the **CPU**
(~5× RT), so the GPU is no longer required — it's an alternative, not the headline. Only
*pure-Python* convolution is too slow (~0.97× RT); the GPU does ≈6× RT at block 256, full
BRIR. If you do use `--gpu`: host Python has `cupy-cuda12x` **plus the matching CUDA 12
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

### Build & deploy to the owner's Windows host — the current runbook

The owner runs the apps on the **Windows host** (native `py -3.12`), driven from the WSL
worktree via the host-local launchers in `C:\Users\ibarz\BinauralHeadtracker\`:
`run.cmd` (a menu) → `run_binaural.cmd` (snare demo) / `run_passthrough.cmd` (world-lock all
system audio) → `launch_live.py` / `launch_passthrough.py`, which inject the permanent WSL
checkout's `src`/`apps` onto `sys.path` and call the app's `main()`. Host assets kept out of
git live there too: `face_landmarker.task`, `dry_snare.wav`, the baked BRIR dir (under the
EqualizerAPO config), and `wheels/` (the built kernel).

Fast path (what the launchers already do): **CPU, no GPU, via the native Rust kernel.**

1. **Build the kernel wheel once** (in WSL): `rust/build-wheels.sh windows` → a
   `cp310-abi3-win_amd64.whl` in `dist/`. (abi3 ⇒ one wheel for any host Python 3.10+.)
2. **Install it into the host Python**: copy the wheel to `C:\…\BinauralHeadtracker\wheels\`
   and `py -3.12 -m pip install --force-reinstall "C:\…\wheels\<wheel>"`. Verify
   `py -3.12 -c "import binaural_kernel"`.
3. **Run**: double-click `run.cmd` (or a `run_*.cmd`). The passthrough uses
   `--rust --lfe-mode brir --output-eq <rev26_final.txt>` at a small `--block` with a low
   `--latency-ms` for snappy head-tracking. `--gpu` (CuPy) stays available as an alternative
   but needs the CUDA-12 wheel set above; plain pure-Python CPU is the last-resort fallback
   (works everywhere, ~0.97× RT, may drop out under motion).

Updating the app's code needs no redeploy — the launchers import live from the WSL checkout;
only a **kernel** change (anything under `rust/`) requires rebuilding + reinstalling the wheel
(steps 1–2). Sharing with a colleague: build their platform's wheel (Linux native / Windows via
cargo-xwin / macOS on a Mac), or they run the pure-Python fallback.

**Latency sweep (automated characterisation).** `run_latency_sweep.cmd` → `launch_sweep.py` →
`apps/latency_sweep.py` runs `apps/latency_bench.py` once per setting with a synthetic pose + sine
(no camera/VoiceMeeter) on the real FiiO output, and prints a ranked table that **splits latency into
stale / queue / device** and flags dropouts + tone integrity. Use it to confirm the WASAPI
shared-vs-exclusive device-buffer finding (journal 42) and to pick block/cushion, instead of hand
edits + restarts. `--output sim` runs headless anywhere (no device buffer).

**In-app reload (so a code change needs no manual restart).** The passthrough returns exit code
`RESTART_EXIT_CODE` (97) when the owner presses **Enter** (the *Reload* button); `run_passthrough.cmd`
wraps the `py` call in a `:loop` that relaunches while it sees 97, so a fresh process picks up the
latest WSL code and re-opens the audio devices cleanly — no closing the window or re-running
`run.cmd`. Quit (**Q**/**Esc**) exits 0 and falls through to `pause`. Any new host launcher that
wants the same must keep the `if %errorlevel%==97 goto loop` line. (Journal 39.)

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
