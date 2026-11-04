//! Console hygiene for the engine's own terminal (Windows-only).
//!
//! When the engine is run directly in a console (the dev / "double-click the exe" workflow, as
//! opposed to the hidden `CREATE_NO_WINDOW` launch the Tauri app uses) that console defaults to
//! **QuickEdit Mode**: clicking or selecting anywhere in the window silently *suspends the whole
//! process* until a key is pressed. A suspended engine stops servicing audio and telemetry, so the
//! cockpit freezes on "Waiting for audio levels…", sound stops, and gain changes never confirm —
//! symptoms indistinguishable from a code regression (journal: a stray terminal click, mistaken for
//! a broken build). Clearing the QuickEdit bit at startup removes that foot-gun for every direct
//! run, regardless of how the engine was launched.

#[link(name = "kernel32")]
extern "system" {
    fn GetStdHandle(nstdhandle: u32) -> isize;
    fn GetConsoleMode(console: isize, mode: *mut u32) -> i32;
    fn SetConsoleMode(console: isize, mode: u32) -> i32;
}

/// Disable the console's QuickEdit Mode so a click/selection can't pause the engine.
///
/// Best-effort and side-effect-free on failure: when the engine has no attached console (e.g. the
/// hidden Tauri launch), `GetConsoleMode` fails and this simply returns `false` without touching
/// anything. `ENABLE_EXTENDED_FLAGS` must be set in the same `SetConsoleMode` call for the
/// QuickEdit bit change to take effect; every other mode bit is preserved. Returns whether the
/// console now has QuickEdit disabled (including the already-disabled case).
pub fn disable_quick_edit() -> bool {
    const STD_INPUT_HANDLE: u32 = 0xFFFF_FFF6; // (DWORD)-10
    const INVALID_HANDLE_VALUE: isize = -1;
    const ENABLE_QUICK_EDIT_MODE: u32 = 0x0040;
    const ENABLE_EXTENDED_FLAGS: u32 = 0x0080;
    unsafe {
        let handle = GetStdHandle(STD_INPUT_HANDLE);
        if handle == 0 || handle == INVALID_HANDLE_VALUE {
            return false;
        }
        let mut mode: u32 = 0;
        // Fails when stdin is not a console (hidden launch, redirected pipe) — nothing to disable.
        if GetConsoleMode(handle, &mut mode) == 0 {
            return false;
        }
        let new_mode = (mode & !ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS;
        if new_mode == mode {
            return true; // already disabled (and extended flags already set)
        }
        SetConsoleMode(handle, new_mode) != 0
    }
}
