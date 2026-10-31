//! Bake the git commit + build time into the engine so the running binary can report exactly
//! which source it was built from (`ENGINE_BUILD` on startup, `version` in `/api/get_config` and
//! `/api/version`, the cockpit footer). This is the foundation of the anti-staleness mechanism:
//! it turns "is the deployed Windows build current?" into a one-line comparison against
//! `git rev-parse HEAD` (see host/deploy-windows.sh + host/check-deploy.sh).
//!
//! Runs on the BUILD host (Linux/WSL), where `git` and `date` are available, for every target —
//! including the Windows cross-build. If git is unavailable (e.g. a source tarball with no .git),
//! it degrades to "unknown" rather than failing the build. An explicit `WLBS_GIT_SHA` in the
//! environment overrides git (for reproducible/CI builds).
use std::process::Command;

fn git(args: &[&str]) -> Option<String> {
    let out = Command::new("git").args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

fn main() {
    let sha = std::env::var("WLBS_GIT_SHA")
        .ok()
        .or_else(|| git(&["rev-parse", "HEAD"]))
        .unwrap_or_else(|| "unknown".into());
    let short = sha.get(..7).map(str::to_string).unwrap_or_else(|| sha.clone());
    let dirty = git(&["status", "--porcelain"]).map_or(false, |s| !s.trim().is_empty());
    // Build time in UTC ISO-8601. `date` is on every build host; no chrono dependency.
    let date = Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "unknown".into());

    println!("cargo:rustc-env=WLBS_GIT_SHA={sha}");
    println!("cargo:rustc-env=WLBS_GIT_SHA_SHORT={short}");
    println!("cargo:rustc-env=WLBS_GIT_DIRTY={}", u8::from(dirty));
    println!("cargo:rustc-env=WLBS_BUILD_DATE={date}");

    // Re-run when HEAD or the index moves so the stamp tracks the checkout (resolved paths work in
    // a linked worktree, where .git is a file and HEAD lives under the main repo's admin dir).
    for p in ["HEAD", "index"] {
        if let Some(path) = git(&["rev-parse", "--git-path", p]) {
            println!("cargo:rerun-if-changed={path}");
        }
    }
    println!("cargo:rerun-if-env-changed=WLBS_GIT_SHA");
}
