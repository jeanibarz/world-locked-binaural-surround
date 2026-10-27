# Host launcher

The owner runs the apps on the **Windows host** (native `py -3.12`), not under WSL
(camera + WASAPI + a display). There is **one** start script — `run.cmd` in this folder —
and it is the only host launcher you need. It replaces the old
`launch_live.py` / `launch_cockpit.py` / `launch_sweep.py` / `launch_passthrough.py`
plus `run.cmd` / `run_binaural.cmd` / `run_cockpit.cmd` / `run_latency_sweep.cmd` /
`run_passthrough.cmd` — all of which were the same few apps with different flag presets.

## Deploy

Copy `run.cmd` into the host install folder next to the owner's assets:

```
C:\Users\ibarz\BinauralHeadtracker\
  run.cmd                 <- this file (double-click or `run.cmd <mode>`)
  face_landmarker.task    <- local C: copy (MediaPipe can't open a UNC path)
  dry_snare.wav
  hrir_sets\              <- dense_*.sofa candidates + report.json (for `test`)
```

`run.cmd` points at the WSL **main** checkout over `\\wsl.localhost\...` and injects
`src`+`apps` onto `sys.path` via `apps/launch.py`, so editing the Python needs no redeploy;
dev worktrees are deleted after merge, so the launcher deliberately does not chase them.
When the `\\wsl.localhost` paths or the EqualizerAPO config paths in `run.cmd` change,
edit the `set` lines at the top of the copied `run.cmd` — the rest is mode dispatch.

## Modes

| command | what it does |
| --- | --- |
| `run.cmd` / `run.cmd listen` | daily world-lock passthrough, **cockpit** UI |
| `run.cmd test` | passthrough + loads `hrir_sets\` for the RFC-0003 **blind A/B & localization test** |

Flags after the mode are forwarded, e.g. `run.cmd listen --method nearest --block 1024`,
`run.cmd listen --ui console`. `listen`/`test` relaunch automatically on the in-app Reload
(Enter → exit 97). See `AGENTS.md` → "Running the host GUI apps" for the host-interop gotchas.
