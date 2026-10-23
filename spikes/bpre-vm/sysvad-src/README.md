# Vendored SYSVAD driver source (Track B virtual audio device)

This directory is the **version-controlled source of truth** for the Track-B kernel changes to
Microsoft's **SYSVAD** sample audio driver (the "Virtual Audio Device (WDM) - Tablet Sample" that the
head-tracker renders surround into). Until now those changes lived only on the host build tree
(`D:\kookr-vm\wds\...`), un-tracked — a disk loss would have lost them, and the kernel change could not
be reviewed or rebuilt from source control (research-journal 72).

## What's here

- **`sysvad_b1b2.patch`** — a `git diff` of our B1/B2 changes against upstream, 6 files:
  | file | change |
  |---|---|
  | `EndpointsCommon/minwavertstream.cpp` / `.h` | **B1 pull-driven mode**: `m_bPullMode`, `PullAdvance()`, `PullThreadRoutine`, the named `\BaseNamedObjects\KookrPullTick` sync event, and clean teardown on PAUSE/dtor |
  | `EndpointsCommon/speakerwavtable.h` | **7.1 LPCM** speaker endpoint (stereo → 8-ch `KSAUDIO_SPEAKER_7POINT1_SURROUND`), journal 69 / `make_speaker_71.py` |
  | `EndpointsCommon/speakertoptable.h` | topology jack mask → 7.1 |
  | `common.cpp` | **Win10 down-level compat** — redirect the two `NTDDI_WIN10_NI`-gated `*NPagedLookasideList` imports to always-exported pool APIs (journal 68) |
  | `adapter.cpp` | `g_DoNotCreateDataFiles = 0` (B-pre: let `ReadBytes` save the device-output WAV for analysis) |

## Upstream base

- Repo: `https://github.com/microsoft/Windows-driver-samples.git` (MIT / MS-PL — permissive)
- Commit: **`bb68ed5e770bce04dc5395ec9258dafab034184d`** (2026-10-20)
- Project path within it: `audio/sysvad/`

## Rebuild from source control

```powershell
# 1. clone upstream at the pinned commit
git clone --filter=blob:none https://github.com/microsoft/Windows-driver-samples.git wds
git -C wds checkout bb68ed5e770bce04dc5395ec9258dafab034184d

# 2. apply our tracked patch
git -C wds apply <repo>/spikes/bpre-vm/sysvad-src/sysvad_b1b2.patch

# 3. build + Win10-compat + 7.1 + sign (the repo's tracked scripts)
spikes/bpre-vm/build_sysvad.ps1          # EWDK build (EndpointsCommon + TabletAudioSample)
spikes/bpre-vm/apply_win10_compat.ps1    # vcxproj NTDDI/sideband down-level edits + import verify
spikes/bpre-vm/make_speaker_71.py        # idempotent 7.1 header patch (already in the diff; safe to re-run)
spikes/bpre-vm/sign_package.ps1          # test-sign into pkg10 (DriverVer 10.0.19041.x)
```

> The `.vcxproj` NTDDI / sideband edits are applied by `apply_win10_compat.ps1` at build time (it leaves
> `*.preNTDDI.bak` / `*.preSideband.bak` backups), so they are reproducible from the tracked scripts and
> are intentionally **not** in the patch.

## Keeping this in sync

When the driver C++ changes on the host build tree, refresh the patch so the repo stays the source of
truth:

```bash
ssh -i ~/.ssh/kookr_host_admin ibarz@$GW \
  "powershell -NoProfile -Command \"cd D:\\kookr-vm\\wds; git diff\"" \
  > spikes/bpre-vm/sysvad-src/sysvad_b1b2.patch
```

Known next change: **issue #104** — move the pull from per-stream to a single device-level pull thread
that advances every active stream per `KookrPullTick` (fixes the 1/N tick-stealing starvation, journal
72). Validate in the Win10 VM (checkpoint) before the host; never unload the driver to clear a stuck
stream — reboot (journal 70).
