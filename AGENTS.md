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

> **The product is 100% Rust — the Python prototype has been removed (`chore/remove-python`,
> RFC G4: zero Python in the delivered product).** The engine is the single standalone binary
> `wlbs_engine.exe` (crates: `rust-engine` + `rust-{render-core-lib,conv-core,control-plane,audio-io,vision-client}`;
> `rust-filterpack-bake` is the zero-Python BRIR baker). **Build every feature in the Rust
> crates.** Do not reintroduce Python, `apps/launch.py`, a PyO3 kernel, or a `run.cmd` launcher —
> that regresses G4. The parity goldens (`rust-engine/hp_eq/oracle_golden_48k.f32`,
> `rust-vision-client/tests/fixtures/kabsch_fixture.json`) are frozen snapshots of the old
> Python oracles, kept as data; recover a generator from git history only if one must be re-derived.

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
  `git worktree add ../world-locked-binaural-surround-<slug> -b <branch> main`
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

- The engine is a Windows binary (`wlbs_engine.exe`, WASAPI + webcam). Build it for
  the host with `cargo xwin` (see `host/bundle-engine.sh`) and run it over interop —
  `wlbs_engine.exe --list-devices` enumerates endpoints from here without a window.
- WSL itself exposes only WSLg PulseAudio (no WASAPI, no soundcard), which is
  **not** representative of the Windows low-latency path — never quote WSL audio
  numbers. Keep portable DSP/geometry in the pure crates (unit-tested on WSL/CI
  without any device via `cargo test`); the WASAPI/vision modules are `cfg(windows)`
  and type-check with `cargo xwin check --target x86_64-pc-windows-msvc`.
- Do **not** record an audio/camera block as "blocked on WSL" — drive the Windows
  engine over interop. (See journal 02.)

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
- **New kernel drivers are validated in the Hyper-V VM first** (checkpoint → install → load), never
  straight onto the host. The one driver now **installed on the host** is the Track-B virtual audio
  device, test-signed, after the owner explicitly authorised a host install and enabled test-signing
  (journals 64 / 68 / 69). Do not install/load *other* kernel drivers on the host.
- **Take a Hyper-V checkpoint before every driver install/load** in the VM (one-command rollback
  of a BSOD/bad driver).
- **Never unload the virtual audio driver to clear a stuck stream — REBOOT instead.** A hung or
  killed host-audio run can leave a **half-open stream** on the SYSVAD sink that pins the kernel
  pull-event `Global\KookrPullTick` (fresh `CreateEvent` → `err=5`) with `audiodg` still attached.
  Uninstalling / removing / disabling / `devcon restart`-ing the driver to force that stream shut
  **bugchecks (BSOD) the host** — it cost a whole session once (journal 70). The stream is stuck, not
  the driver; a reboot clears it cleanly. Any host-audio harness must guarantee stream teardown on
  **every** exit path and never hang unbounded.
- **Never touch host boot config** (`bcdedit`, Secure Boot) or the host's default audio device /
  VoiceMeeter routing. (Test-signing is on for the Track-B device above; do not toggle it off blindly
  — other work may depend on it.)
- **Confirm with the owner before anything irreversible or outward-facing** on the host (deleting
  a VM, uninstalling tools or the audio driver, network-exposing changes). Installs, VM creation,
  checkpoints, and in-VM work are fine to do autonomously.

### Running the engine on the host (camera + audio + a window) — ON WINDOWS, not WSL

The engine needs a **real webcam, a real audio output, and a display**, so it **must run on
the Windows host**, never under WSL (no `/dev/video*`, no WASAPI). It is a single standalone
binary — `wlbs_engine.exe` — with no interpreter, no `sys.path`, and no model-path gotchas:
it resolves its own assets (ONNX models, BRIR packs, `hp_eq/`, `web_cockpit/`) relative to its
exe. Drive it through WSL interop:

1. **Build + bundle from WSL:** `host/bundle-engine.sh <out_dir>` cross-compiles the engine
   with `cargo xwin` and assembles the self-contained bundle (exe + `DirectML.dll` + `assets/`
   + `packs/default/` + `hp_eq/` + `web_cockpit/`). Copy the bundle to the owner's host folder
   (below).
2. **Run it** — `wlbs_engine.exe` prints `COCKPIT http://127.0.0.1:8732` and serves the cockpit
   there. Launch it with the Bash tool's **`run_in_background`** (it's long-lived; the
   completion notice is the user quitting or a crash to read). `wlbs_engine.exe --list-devices`
   enumerates WASAPI endpoints from here without a window.

**The owner's host install location is `C:\Users\ibarz\WLBS-Engine\`** (per-user; Program Files
needs admin we don't have). Put new host assets (BRIR packs, hp_eq profiles) there, or import
them into `%APPDATA%\WLBS` with `wlbs_engine --import-pack <dir>` / `--import-hp-eq <dir>`.

**Anything visual (alignment, symmetry, text width, clipping, DPI) is checked by screenshot +
pixel measurement, never by reasoning about the math or by an isolated probe** — load the
`host-gui-visual-check` skill.

### Measurements & latency experiments — headless, run them here too

**"Needs host hardware" in the backlog/journals (e.g. B8.5, the latency split, B8.8) means
*run it on the host via interop* — NOT "blocked / deferred".** Don't write "needs host
hardware, deferred" without first running it from here:

- **Device buffer + split, unattended:** the engine's own live latency probe splits
  pose-age-at-the-ear into **stale / queue / device** (shown in the cockpit and printed on exit;
  journals 46/85).
- **The live `queue`/`stale` audio-pipeline terms do NOT need a face.** The camera only sets a
  pose *value + timestamp*; it does not touch FIFO timing — a synthetic `pose_t` (~30 Hz) over
  real VoiceMeeter loopback + real output exercises them with no camera, no window.
- The only things you genuinely can't do from here are a **human moving their head** and judging
  audio **by ear** (the motion→sound ground-truth, B8.5 §c). Everything else is runnable now.

### Build & deploy to the owner's Windows host — the runbook

The product is **CPU, no GPU, no Python** — the Rust render core sustains the 8-channel
500 ms-BRIR passthrough in real time on the CPU. The runbook:

1. **Build the bundle** (WSL): `host/bundle-engine.sh <out_dir> [onnx_dir] [canonical] [pack]`.
   A **native-ext4 `CARGO_TARGET_DIR`** is recommended (`llvm-dlltool` EACCESes on `/mnt/*`).
   The pure DSP/geometry builds and unit-tests on WSL with `cargo test`; the `cfg(windows)`
   WASAPI/vision modules type-check with `cargo xwin check --target x86_64-pc-windows-msvc`.
2. **Copy to the host** install folder (`C:\Users\ibarz\WLBS-Engine\`) and run
   `wlbs_engine.exe`. Defaults match the owner's listening setup (`timealign`, headphone comp
   on); override devices with `--source`/`--output`, port with `--port`, interpolation with
   `--method`, block/cushion with `--block`/`--latency-ms`.
3. **Native window (optional):** `host/build-tauri.sh` builds the `src-tauri/` shell, which
   launches `wlbs_engine.exe` (resolved beside its own exe) hidden on port 8732 and shows the
   cockpit in a single native window; closing the window stops the engine.

A code change requires a **rebuild + rebundle** (the engine is compiled — there is no live
import). Live settings (camera, flip, HRIR set, headphone comp, **per-position ITD**)
reconfigure the running engine from the cockpit's Settings panel with no restart. Cross-platform:
the engine builds for Linux natively and Windows via `cargo xwin`; macOS needs a Mac (Track S).

**NON-NEGOTIABLE — never leave the Windows app stale.** The engine is a compiled binary, so
"merged to main" is NOT "running on Windows" — there is a build+copy gap that silently bit us
repeatedly. The guardrails:
- **Deploy with `host/deploy-windows.sh`** — the one blessed in-place updater (build → copy
  exe + `web_cockpit/` → write `DEPLOYED_SHA`; stages `wlbs_engine.new.exe` if the engine is
  running, which `run-engine.bat` auto-applies on next launch). Run it after ANY change under
  `rust-engine/` or `apps/web_cockpit/`, and ALWAYS **before telling the owner to test**.
- **Every build is stamped** (git SHA + date, baked by `rust-engine/build.rs`): printed as
  `ENGINE_BUILD …` on startup, served at `/api/version`, and shown in the cockpit SYSTEM panel's
  "Build" row. To confirm what is live, read that row or curl `/api/version` and compare to
  `git rev-parse HEAD`.
- **`host/check-deploy.sh`** reports (exit 2) when the install is stale; a **Stop hook**
  (`.claude/settings.json` → `host/stop-hook-deploy-check.sh`) runs it every turn and blocks the
  stop with a reminder when a redeploy is owed — so forgetting is not possible. If it fires,
  redeploy (or say explicitly why you are not) before finishing.

## Working discipline

- **Build in Rust** — the product is the Rust engine and the Python prototype has been
  removed (RFC G4). Add features in the right crate: DSP in `rust-render-core-lib` /
  `rust-conv-core`, the pipeline/CLI/settings in `rust-engine`, the cockpit/HTTP in
  `rust-control-plane`, WASAPI in `rust-audio-io`, head tracking in `rust-vision-client`,
  pack baking in `rust-filterpack-bake`. Keep config/HRIR/world-model shared across crates.
  Record any new third-party licence in the journal.
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
guess which `saved_model/*.keras` to use** — resolve the best validated judge from the
registry's own metadata, or via the sibling project `binaural-localization` (which owns
the judge tooling and loads the keras model in its env). Current best:
**`pooled5_distribution_v1`** (pooled 5-rig, cross-rig QE 9.0%, in-domain 0.44%). Judging
is a sibling-project analysis workflow — it is **not** part of this (Rust, zero-Python) product.

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
