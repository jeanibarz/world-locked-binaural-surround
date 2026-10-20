# 54 — Elevated host control via an SSH admin channel (max-autonomy for Track B)

**Block:** B8.7b (RFC Track B, infra) · **Milestone:** M8 / Track B · **Status:** done — the
channel is live and verified (elevated shell confirmed). Unblocks the WDK install + Hyper-V VM
creation that B-pre needs.

## Context

Track B (the custom audio driver, journal 53) needs **admin** on the Windows host: install the
WDK, create/manage a Hyper-V VM, test-sign and load drivers. WSL interop runs **non-elevated**
and can't be elevated without relaunching the session — and the session is a Kookr-spawned
streaming Claude Code process the owner can't easily restart elevated. The owner asked for
**maximum autonomy** ("do everything yourself").

## What we did

Established a **key-only SSH channel from WSL into the Windows host that carries a full admin
token** — so an unprivileged WSL session gets elevated host control without a restart. Mechanism:

- A dedicated keypair in WSL (`~/.ssh/kookr_host_admin`, ed25519; private key never leaves the
  box, never committed).
- A one-time **elevated** host script (`spikes/host_admin_sshd_setup.ps1`, run by the owner via a
  file + a one-line launch because multi-line paste into their terminal was unreliable) that:
  installs/starts **OpenSSH Server**; authorizes the public key in
  `C:\ProgramData\ssh\administrators_authorized_keys` (ACL set via **SIDs**, since the host is
  French — `Administrators` doesn't resolve by name); sets **key-only** auth; sets
  **`LocalAccountTokenFilterPolicy=1`** so a local-admin network logon gets a full (un-UAC-filtered)
  token; and firewalls port 22 to the **WSL subnet** (`172.16.0.0/12`).
- The script logs a transcript to `setup_sshd.log`, which this session read back to verify every
  step (copy-paste was unreliable; a logged script file was the fix the owner suggested).

**Verified:** `ssh -i ~/.ssh/kookr_host_admin ibarz@172.23.208.1 "powershell … IsInRole(Administrator)"`
→ `jean-pc1\ibarz`, `Elevated=True`. Host reached at the WSL default-gateway IP (`172.23.208.1`
this session; rediscover each session — it changes across WSL restarts). Remote default shell is
`cmd.exe`; PowerShell is wrapped as `powershell -NoProfile -Command "…"`.

## Safety contract (recorded in AGENTS.md; NON-NEGOTIABLE)

The channel holds real admin, so:
- **Kernel drivers load ONLY inside the Hyper-V VM, never the host** — host keeps Secure Boot on /
  test-signing off, so host audio and anti-cheat are never disturbed.
- **Checkpoint the VM before every driver install/load** (one-command BSOD rollback).
- **Never touch host boot config** (`bcdedit` / test-signing / Secure Boot) or the host's default
  audio device / VoiceMeeter routing.
- **Confirm before anything irreversible or outward-facing** on the host (deleting a VM,
  uninstalling tools, network-exposing changes). Installs, VM creation, checkpoints, in-VM work:
  autonomous.

## Why this pattern (and not alternatives)

- **Not** a second Claude Code "agent" on the host: two reasoning agents relaying prose is lossy,
  slow, and costly — I need host *execution*, not a second brain. A shell beats it.
- **Not** elevated WSL interop: can't elevate without a session restart, which wasn't available.
- SSH gives a fresh network logon that *can* carry a full admin token (unlike interop), so it was
  the clean way to get elevation into the existing session.

## Decision / next

- Channel live. AGENTS.md documents how to connect + the safety contract; `spikes/` holds the
  setup + revert scripts; `sshd` can be stopped anytime to cut access.
- **Next (autonomous):** install the WDK on the host, create + provision the Hyper-V VM, then
  author/build the SYSVAD pull-driven fork and run the B-pre audiodg-tolerance test — checkpointing
  before each driver load, checking in at the B-pre go/no-go (journal 53).
- To tear down when Track B is done: `spikes/host_admin_sshd_revert.ps1` (elevated).
