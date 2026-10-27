//! Install-relative asset + config resolution, so the standalone engine is a clean-machine
//! distributable with **no hardcoded paths** (the thing that made the old `run.cmd` non-portable).
//!
//! Resolution order for each asset: explicit CLI arg → environment variable → next to the executable
//! (the bundled install layout) → `%APPDATA%\WLBS\` (user data). The bundle ships `assets/` (ONNX
//! nets + canonical model), `web_cockpit/`, and `packs/default/` beside the exe; user-imported packs
//! live under `%APPDATA%\WLBS\packs`.

use std::path::{Path, PathBuf};

/// Directory containing the running executable (the install root), or `.` if unknown.
pub fn exe_dir() -> PathBuf {
    std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)).unwrap_or_else(|| PathBuf::from("."))
}

/// `%APPDATA%\WLBS` on Windows; `$HOME/.config/WLBS` elsewhere (for tests). The per-user data root.
pub fn appdata_wlbs() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("WLBS")
}

/// Resolve a directory asset: `cli` → env `$env_key` → `<exe_dir>/<rel>` (if it exists) →
/// `%APPDATA%\WLBS\<rel>`. Returns the first that resolves (existence only gates the install-relative
/// step, so a user override via CLI/env always wins even if absent, surfacing a clear error later).
pub fn resolve_dir(cli: Option<&str>, env_key: &str, rel: &str) -> PathBuf {
    if let Some(c) = cli {
        return PathBuf::from(c);
    }
    // A set-but-empty variable counts as unset (else it'd be the current dir and beat the fallbacks).
    if let Some(e) = std::env::var_os(env_key).filter(|v| !v.is_empty()) {
        return PathBuf::from(e);
    }
    let near = exe_dir().join(rel);
    if near.exists() {
        return near;
    }
    appdata_wlbs().join(rel)
}

/// Resolve a single file asset the same way (install-relative `<exe_dir>/<rel>` preferred).
pub fn resolve_file(cli: Option<&str>, env_key: &str, rel: &str) -> PathBuf {
    if let Some(c) = cli {
        return PathBuf::from(c);
    }
    // A set-but-empty variable counts as unset (else it'd be the current dir and beat the fallbacks).
    if let Some(e) = std::env::var_os(env_key).filter(|v| !v.is_empty()) {
        return PathBuf::from(e);
    }
    let near = exe_dir().join(rel);
    if near.exists() {
        return near;
    }
    appdata_wlbs().join(rel)
}

/// Process-global lock serialising every test that mutates the shared `APPDATA` env var the asset
/// resolvers read (the `packs` and `hp_profiles` stores both do). A per-module lock only serialises
/// its own module's tests, so two modules racing `set_var`/`remove_var` on `APPDATA` would clobber
/// each other under cargo's parallel runner — this one lock is shared by both.
#[cfg(test)]
pub(crate) static ENV_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_arg_wins() {
        assert_eq!(resolve_dir(Some("/explicit"), "NOPE_ENV", "assets"), PathBuf::from("/explicit"));
    }

    #[test]
    fn env_beats_install_relative() {
        std::env::set_var("WLBS_TEST_ENV_X", "/from/env");
        assert_eq!(resolve_dir(None, "WLBS_TEST_ENV_X", "assets"), PathBuf::from("/from/env"));
        std::env::remove_var("WLBS_TEST_ENV_X");
    }

    #[test]
    fn empty_env_is_unset() {
        std::env::set_var("WLBS_TEST_ENV_EMPTY", "");
        assert_ne!(resolve_dir(None, "WLBS_TEST_ENV_EMPTY", "assets"), PathBuf::new());
        assert_ne!(resolve_file(None, "WLBS_TEST_ENV_EMPTY", "x.onnx"), PathBuf::new());
        std::env::remove_var("WLBS_TEST_ENV_EMPTY");
    }

    #[test]
    fn appdata_has_wlbs_suffix() {
        assert!(appdata_wlbs().ends_with("WLBS"));
    }
}
