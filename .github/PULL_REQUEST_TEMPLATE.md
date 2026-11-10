<!--
Fill the summary and verification, then work every marked checklist row below.

The kookr:check markers on the checklist rows are verified by the PR checklist
gate (`kookr pr-checklist verify`, run on `gh pr create`) against the actual
diff. A CHECKED box is an assertion that the evidence is in this PR; a STRUCK
row with a one-line reason on the same line is a waiver; a blank marked box
fails. Keep each marked row on ONE line and its marker verbatim. Build the body
from this template and pass it with `--body-file` (a file inside the worktree) or
a quoted heredoc; an inline `--body` skips the template and the gate.
-->

## Summary

<!-- Plain-language intent first (2-4 sentences): what problem, what changed, why it matters.
     Name the backlog block / RFC slice / issue (e.g. "Part of #NNN", "Journal NN"). Define jargon on
     first use; put crate/function names in the bullets below. -->

## Verification

<!-- Evidence, not assertions: exact commands + outcomes (e.g. `cargo test -p rust-vision-client` -> 34/34,
     `cargo xwin check --target x86_64-pc-windows-msvc` green) and, for anything that touches audio or
     camera, the live-on-host result (the Windows engine runs from WSL over interop). -->

## Pre-merge checklist

- [ ] <!-- kookr:check:cargo-green --> `cargo test` (touched crates) and `cargo fmt` are green locally; for `cfg(windows)` code also `cargo xwin check --target x86_64-pc-windows-msvc`. Outcome stated in Verification.
- [ ] <!-- kookr:check:docs --> Research-journal entry (+ index, `LEARNINGS.md`) and `docs/backlog.md` updated for this block, or the journal lands in a paired docs PR.
- [ ] <!-- kookr:check:redeployed --> Any change under `rust-engine/` or `apps/web_cockpit/` was rebuilt and relaunched with `host/redeploy.sh` (the live engine is not stale).
- [ ] <!-- kookr:check:deps-licensed --> No new dependency, or each new one is licence-compatible (MIT/BSD/Apache; flag GPL) and recorded with its licence in the journal.
- [ ] <!-- kookr:check:no-large-binaries --> No SOFA/wav/model/pack binaries, secrets or SSH keys committed (fetch-and-rebuild steps are documented instead).
