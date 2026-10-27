//! Named headphone-compensation profile discovery + import, mirroring the BRIR-pack store in
//! [`crate::packs`] (#240). A **profile** is a directory holding an EqualizerAPO config plus any
//! `Include:`/`Convolution:` FIR files it references, from which [`crate::hp_eq::load_output_eq`]
//! builds the post-downmix per-ear comp/EQ chain.
//!
//! Two locations, exactly like packs:
//! - the **bundled default** — the install-relative `hp_eq/` directory resolved in `main.rs`
//!   (CLI `--hp-eq-dir` → `WLBS_HP_EQ_DIR` → `<exe>/hp_eq` → `%APPDATA%\WLBS\hp_eq`), listed and
//!   resolved under the reserved name `default`; and
//! - **user profiles** under `%APPDATA%\WLBS\hp_eq\<name>\`, each a subdirectory with its own
//!   config, installed with [`import_profile`] (the `--import-hp-eq` analogue of `--import-pack`).
//!
//! The config file inside a profile directory is found by canonical name (see
//! [`PROFILE_CONFIG_NAMES`]): a freshly imported profile is expected to carry `config.txt`, and the
//! owner's bundled profile keeps its historical `baked_7p1_rev26_final.txt`. Discovery and import
//! are deliberately *structural* (does a readable config file exist?) — a config that parses but
//! yields no L/R chain is surfaced later as `HP_EQ_WARN` at load time and degrades gracefully, the
//! same way a broken bundle already does. This keeps the module pure and native-testable (the
//! Windows audio glue is `#![cfg(windows)]`), like `packs`.

use std::path::{Path, PathBuf};

use crate::assets::appdata_wlbs;

/// Config filenames a profile directory may use, in priority order: `config.txt` for imported
/// profiles, and the owner's historical bundled name as a fallback. The config file's own name is
/// never referenced by `Include:`/`Convolution:` lines (those are resolved relative to it), so the
/// two names are interchangeable from the loader's point of view.
const PROFILE_CONFIG_NAMES: [&str; 2] = ["config.txt", "baked_7p1_rev26_final.txt"];

/// `%APPDATA%\WLBS\hp_eq` — the user profile directory (created on demand by an import).
pub fn user_hp_eq_dir() -> PathBuf {
    appdata_wlbs().join("hp_eq")
}

/// A regular file that is not a symlink (see [`crate::packs`] — [`import_profile`] skips symlinks
/// when copying, so counting a symlinked config/FIR as present would let a source validate and then
/// import as an incomplete profile).
fn is_real_file(p: &Path) -> bool {
    std::fs::symlink_metadata(p).map(|m| m.is_file()).unwrap_or(false)
}

/// The EqualizerAPO config file inside a profile directory, if one is present under a canonical
/// name. This is the path handed straight to [`crate::hp_eq::load_output_eq`], so the loader
/// resolves the profile's `Include:`/`Convolution:` FIRs relative to this directory.
pub fn profile_config(dir: &Path) -> Option<PathBuf> {
    if !dir.is_dir() {
        return None;
    }
    PROFILE_CONFIG_NAMES.iter().map(|n| dir.join(n)).find(|p| is_real_file(p))
}

/// Does `dir` hold a profile the engine can load? Structural, like [`crate::packs::is_pack_dir`]:
/// it must contain a readable config file at a canonical name. (A config that parses but produces
/// no L/R comp/EQ chain is caught at load time with `HP_EQ_WARN`, not here.)
pub fn is_hp_profile_dir(dir: &Path) -> bool {
    profile_config(dir).is_some()
}

/// Validate a user-supplied profile name: a single path component, no separators / `..` / drive
/// colon, so an import or resolve can't escape the profile directory (the BUG-26 path-traversal
/// guard from `packs`). `default` is reserved for the bundled profile (a user profile of that name
/// could never be resolved).
fn valid_profile_name(name: &str) -> bool {
    !name.is_empty()
        && name != "default"
        && name != "."
        && name != ".."
        && !name.contains('/')
        && !name.contains('\\')
        && !name.contains(':')
        && !name.contains('\0')
        // Win32 strips trailing dots/spaces, so `"..."`/`".. "` could normalise to `..`/empty — reject.
        && name.trim_end_matches(['.', ' ']) == name
}

/// Recursively copy `src` into `dst` (both directories), preserving nested files (the FIR WAVs a
/// comp config `Include:`s / convolves usually live in a subdirectory). Symlinked entries are
/// skipped so a symlink cycle in the source can't make the copy recurse forever. (Identical to
/// `packs::copy_dir_recursive`.)
fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<(), String> {
    for entry in std::fs::read_dir(src).map_err(|e| e.to_string())? {
        let p = entry.map_err(|e| e.to_string())?.path();
        let Some(name) = p.file_name() else { continue };
        let meta = std::fs::symlink_metadata(&p).map_err(|e| format!("stat {}: {e}", p.display()))?;
        if meta.file_type().is_symlink() {
            continue; // don't follow symlinks (cycle-safe)
        }
        let to = dst.join(name);
        if meta.is_dir() {
            std::fs::create_dir_all(&to).map_err(|e| format!("create {}: {e}", to.display()))?;
            copy_dir_recursive(&p, &to)?;
        } else if meta.is_file() {
            std::fs::copy(&p, &to).map_err(|e| format!("copy {}: {e}", p.display()))?;
        }
    }
    Ok(())
}

/// A discovered headphone-comp profile: its display name and directory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HpProfileInfo {
    pub name: String,
    pub dir: PathBuf,
}

/// All available profiles: the bundled default (named `default`, if `default_dir` holds a config)
/// followed by each profile subdirectory in the user dir, sorted by name. `default_dir` is the
/// install-relative `hp_eq/` directory resolved by the caller (honouring `--hp-eq-dir` / the env
/// override), so the listing reflects whatever the operator pointed the engine at.
pub fn list_profiles(default_dir: &Path) -> Vec<HpProfileInfo> {
    let mut out = Vec::new();
    if is_hp_profile_dir(default_dir) {
        out.push(HpProfileInfo { name: "default".into(), dir: default_dir.to_path_buf() });
    }
    if let Ok(rd) = std::fs::read_dir(user_hp_eq_dir()) {
        let mut user: Vec<HpProfileInfo> = rd
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| is_hp_profile_dir(p))
            // `default` is reserved for the bundled profile above; a user subdir of that name (or a
            // bare config file dropped straight into the user dir — which `read_dir` yields as a
            // non-directory entry and `is_hp_profile_dir` rejects) must not shadow it.
            .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy() != "default"))
            .map(|p| HpProfileInfo { name: p.file_name().unwrap().to_string_lossy().into_owned(), dir: p })
            .collect();
        user.sort_by(|a, b| a.name.cmp(&b.name));
        out.extend(user);
    }
    out
}

/// Resolve a profile by name to its **config file** path (ready for
/// [`crate::hp_eq::load_output_eq`]): `default` → the bundled `default_dir`'s config; otherwise
/// `%APPDATA%\WLBS\hp_eq\<name>`'s config. Returns `None` if the name is invalid or the profile has
/// no readable config (gone / never installed), which the caller turns into a graceful fallback.
pub fn resolve_profile(name: &str, default_dir: &Path) -> Option<PathBuf> {
    if name == "default" {
        return profile_config(default_dir);
    }
    if !valid_profile_name(name) {
        return None;
    }
    profile_config(&user_hp_eq_dir().join(name))
}

/// Import a profile directory into `%APPDATA%\WLBS\hp_eq\<name>` (name defaults to the source
/// basename). Mirrors [`crate::packs::import_pack`] exactly: refuses a source that is not a loadable
/// profile and a name that would escape the profile dir; stages the copy in a temp sibling and
/// validates it BEFORE touching any existing profile (so a failed/incomplete import never destroys
/// what the user already has), then swaps it in; nested files are copied; and a self-import is
/// refused rather than deleting the source. Returns the destination directory.
pub fn import_profile(src: &Path, name: Option<&str>) -> Result<PathBuf, String> {
    if !is_hp_profile_dir(src) {
        return Err(format!(
            "{} is not a loadable headphone-comp profile (needs an EqualizerAPO config named {})",
            src.display(),
            PROFILE_CONFIG_NAMES[0],
        ));
    }
    let name = name
        .map(str::to_string)
        .or_else(|| src.file_name().map(|s| s.to_string_lossy().into_owned()))
        .ok_or_else(|| "cannot determine profile name".to_string())?;
    if !valid_profile_name(&name) {
        return Err(format!("invalid profile name {name:?} (no path separators, '..', or drive colon)"));
    }
    let root = user_hp_eq_dir();
    let dst = root.join(&name);
    // Refuse importing a profile onto itself (or a nested path): swapping the destination out would
    // otherwise delete the source. Only possible when `dst` already exists (so we can canonicalize).
    if dst.exists() {
        let src_c = src.canonicalize().map_err(|e| format!("resolve {}: {e}", src.display()))?;
        let dst_c = dst.canonicalize().map_err(|e| format!("resolve {}: {e}", dst.display()))?;
        if src_c == dst_c || src_c.starts_with(&dst_c) || dst_c.starts_with(&src_c) {
            return Err(format!("refusing to import {} onto itself", dst.display()));
        }
    }
    // Copy into a temp sibling and validate it BEFORE touching the existing profile, so a failed or
    // incomplete import never destroys what the user already has.
    let tmp = root.join(format!("{name}.tmp-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    let staged = std::fs::create_dir_all(&tmp)
        .map_err(|e| format!("create {}: {e}", tmp.display()))
        .and_then(|_| copy_dir_recursive(src, &tmp))
        .and_then(|_| {
            if is_hp_profile_dir(&tmp) {
                Ok(())
            } else {
                Err(format!("import produced an incomplete profile from {} (missing config?)", src.display()))
            }
        })
        .and_then(|_| {
            if dst.exists() {
                std::fs::remove_dir_all(&dst).map_err(|e| format!("clear {}: {e}", dst.display()))?;
            }
            std::fs::rename(&tmp, &dst).map_err(|e| format!("install {}: {e}", dst.display()))
        });
    if let Err(e) = staged {
        let _ = std::fs::remove_dir_all(&tmp);
        return Err(e);
    }
    Ok(dst)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Serialises the tests that point `APPDATA` at a temp dir (process-global env). Shared with the
    /// sibling `packs` store so the two modules' APPDATA-mutating tests don't race each other.
    use crate::assets::ENV_TEST_LOCK as ENV_LOCK;

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!("wlbs_hp_eq_test_{}_{}", std::process::id(), rand_suffix()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }
    fn rand_suffix() -> u64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().subsec_nanos() as u64
    }

    /// Write a minimal profile directory: a config file (under the given name) plus a nested FIR
    /// file, so a recursive import has something to preserve. The content need not parse — discovery
    /// and import are structural.
    fn write_profile(dir: &Path, config_name: &str) {
        std::fs::create_dir_all(dir.join("binaural/comp")).unwrap();
        std::fs::write(dir.join(config_name), b"Channel: L R\nPreamp: -4 dB\nInclude: binaural\\comp\\x.txt\n").unwrap();
        std::fs::write(dir.join("binaural/comp/x.txt"), b"Convolution: fir.wav\n").unwrap();
        std::fs::write(dir.join("binaural/comp/fir.wav"), b"RIFF....WAVE").unwrap();
    }

    #[test]
    fn recognises_both_canonical_config_names() {
        let root = tmp();
        let a = root.join("a");
        write_profile(&a, "config.txt");
        assert!(is_hp_profile_dir(&a));
        assert_eq!(profile_config(&a), Some(a.join("config.txt")));

        let b = root.join("b");
        write_profile(&b, "baked_7p1_rev26_final.txt");
        assert!(is_hp_profile_dir(&b));
        assert_eq!(profile_config(&b), Some(b.join("baked_7p1_rev26_final.txt")));

        // config.txt wins when both are present (priority order).
        std::fs::write(b.join("config.txt"), b"Channel: L R\n").unwrap();
        assert_eq!(profile_config(&b), Some(b.join("config.txt")));

        // A directory with no canonical config is not a profile.
        let c = root.join("c");
        std::fs::create_dir_all(&c).unwrap();
        std::fs::write(c.join("readme.txt"), b"hi").unwrap();
        assert!(!is_hp_profile_dir(&c));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn lists_default_and_user_profiles_sorted() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let default_dir = root.join("install_hp_eq");
        write_profile(&default_dir, "baked_7p1_rev26_final.txt");
        std::env::set_var("APPDATA", root.join("appdata"));
        write_profile(&user_hp_eq_dir().join("zeta"), "config.txt");
        write_profile(&user_hp_eq_dir().join("alpha"), "config.txt");
        // A junk subdir (no config) is ignored.
        std::fs::create_dir_all(user_hp_eq_dir().join("junk")).unwrap();

        let names: Vec<String> = list_profiles(&default_dir).into_iter().map(|p| p.name).collect();
        assert_eq!(names, vec!["default", "alpha", "zeta"], "default first, then user profiles sorted");
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn resolves_names_to_config_paths() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let default_dir = root.join("install_hp_eq");
        write_profile(&default_dir, "baked_7p1_rev26_final.txt");
        std::env::set_var("APPDATA", root.join("appdata"));
        write_profile(&user_hp_eq_dir().join("mine"), "config.txt");

        assert_eq!(resolve_profile("default", &default_dir), Some(default_dir.join("baked_7p1_rev26_final.txt")));
        assert_eq!(resolve_profile("mine", &default_dir), Some(user_hp_eq_dir().join("mine").join("config.txt")));
        assert_eq!(resolve_profile("ghost", &default_dir), None, "a missing profile does not resolve");
        // Traversal / absolute names must not resolve.
        assert_eq!(resolve_profile("../evil", &default_dir), None);
        assert_eq!(resolve_profile("a/b", &default_dir), None);
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn default_resolves_from_the_appdata_fallback_without_shadowing_user_profiles() {
        // When the install `hp_eq/` is absent, the caller resolves `default_dir` to the user dir
        // itself (a bare config dropped straight in it — the old on-disk workaround). That config
        // lists as `default`; the subdirectories beside it stay distinct user profiles.
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        std::env::set_var("APPDATA", root.join("appdata"));
        let udir = user_hp_eq_dir();
        std::fs::create_dir_all(&udir).unwrap();
        std::fs::write(udir.join("config.txt"), b"Channel: L R\nPreamp: -4 dB\n").unwrap(); // the "default" config, directly in hp_eq/
        write_profile(&udir.join("mine"), "config.txt");

        let names: Vec<String> = list_profiles(&udir).into_iter().map(|p| p.name).collect();
        assert_eq!(names, vec!["default", "mine"]);
        assert_eq!(resolve_profile("default", &udir), Some(udir.join("config.txt")));
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn imports_and_clears_on_reimport() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let src = root.join("exported");
        write_profile(&src, "config.txt");
        std::env::set_var("APPDATA", root.join("appdata"));

        let dst = import_profile(&src, Some("my-cans")).unwrap();
        assert!(dst.ends_with("WLBS/hp_eq/my-cans") || dst.ends_with("WLBS\\hp_eq\\my-cans"));
        assert!(dst.join("config.txt").is_file());
        assert!(dst.join("binaural/comp/fir.wav").is_file(), "nested FIR files are copied");
        assert_eq!(resolve_profile("my-cans", &root), Some(dst.join("config.txt")));

        // A stale file in the destination is cleared on re-import.
        std::fs::write(dst.join("STALE.txt"), b"old").unwrap();
        let dst2 = import_profile(&src, Some("my-cans")).unwrap();
        assert_eq!(dst2, dst);
        assert!(!dst.join("STALE.txt").exists(), "re-import must clear stale files");

        // Importing a profile onto itself must be refused, not delete it.
        assert!(import_profile(&dst, Some("my-cans")).is_err());
        assert!(is_hp_profile_dir(&dst), "a refused self-import must leave the profile intact");
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn rejects_reserved_and_traversal_names() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let src = root.join("exported");
        write_profile(&src, "config.txt"); // a valid source, so the name check is what fails
        std::env::set_var("APPDATA", root.join("appdata"));
        for bad in ["default", "../evil", "a/b", "..", ".", "C:evil", ""] {
            assert!(import_profile(&src, Some(bad)).is_err(), "name {bad:?} must be refused");
        }
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn non_profile_import_refused() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let src = root.join("notaprofile");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join("notes.txt"), b"not a config").unwrap(); // no canonical config name
        std::env::set_var("APPDATA", root.join("appdata"));
        assert!(import_profile(&src, None).is_err());
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }
}
