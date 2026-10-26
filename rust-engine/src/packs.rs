//! BRIR pack discovery + import under `%APPDATA%\WLBS\packs`, plus the bundled `packs/default`.
//!
//! The engine scans the user pack dir at startup (and the bundled default) so a user can drop in or
//! import more packs without touching the install. A "pack" is a directory holding either the baked
//! per-speaker WAVs (`<ch>_L.wav`) or a `filterpack.json` manifest (the M-D1 format). Import copies a
//! source directory into the user pack dir under a chosen name.

use std::path::{Path, PathBuf};

use crate::assets::{appdata_wlbs, exe_dir};

/// `%APPDATA%\WLBS\packs` — the user pack directory (created on demand).
pub fn user_packs_dir() -> PathBuf {
    appdata_wlbs().join("packs")
}

/// The bundled default pack shipped beside the exe (`<exe_dir>/packs/default`).
pub fn bundled_default() -> PathBuf {
    exe_dir().join("packs").join("default")
}

/// Does `dir` hold a pack the engine can actually load? This must agree with [`BrirPack::load`],
/// which reads `<ch>_L.wav` + `<ch>_R.wav` for every 7.1 channel — so we require that full baked set
/// here (BUG-16). Accepting a `filterpack.json`-only or partial-WAV directory made the engine list
/// and resolve a pack it then failed to open with a confusing `L_L.wav` error; the `filterpack.json`
/// manifest path is not implemented in the loader, so it is not treated as a pack.
pub fn is_pack_dir(dir: &Path) -> bool {
    dir.is_dir()
        && crate::geometry::CHANNELS_7P1.iter().all(|ch| {
            is_real_file(&dir.join(format!("{ch}_L.wav"))) && is_real_file(&dir.join(format!("{ch}_R.wav")))
        })
}

/// A regular file that is not a symlink. [`import_pack`] skips symlinks when copying, so counting a
/// symlinked WAV as present would let a source validate and then import as an incomplete pack.
fn is_real_file(p: &Path) -> bool {
    std::fs::symlink_metadata(p).map(|m| m.is_file()).unwrap_or(false)
}

/// Validate a user-supplied pack name: a single path component, no separators / `..` / drive colon,
/// so an import or resolve can't escape the packs directory (BUG-26 path traversal). `default` is
/// reserved for the bundled pack (a user pack of that name could never be resolved).
fn valid_pack_name(name: &str) -> bool {
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

/// Recursively copy `src` into `dst` (both directories), preserving nested files. Symlinked entries
/// are skipped so a symlink cycle in the source can't make the copy recurse forever.
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

/// A discovered pack: its display name and directory.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackInfo {
    pub name: String,
    pub dir: PathBuf,
}

/// All available packs: the bundled default (named "default", if present) followed by each pack in
/// the user dir, sorted by name.
pub fn list_packs() -> Vec<PackInfo> {
    let mut out = Vec::new();
    let def = bundled_default();
    if is_pack_dir(&def) {
        out.push(PackInfo { name: "default".into(), dir: def });
    }
    if let Ok(rd) = std::fs::read_dir(user_packs_dir()) {
        let mut user: Vec<PackInfo> = rd
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| is_pack_dir(p))
            .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy() != "default"))
            .map(|p| PackInfo { name: p.file_name().unwrap().to_string_lossy().into_owned(), dir: p })
            .collect();
        user.sort_by(|a, b| a.name.cmp(&b.name));
        out.extend(user);
    }
    out
}

/// Resolve a pack by name: "default" → the bundled default; otherwise `%APPDATA%\WLBS\packs\<name>`.
pub fn resolve_pack(name: &str) -> Option<PathBuf> {
    if name == "default" {
        let d = bundled_default();
        return is_pack_dir(&d).then_some(d);
    }
    if !valid_pack_name(name) {
        return None;
    }
    let d = user_packs_dir().join(name);
    is_pack_dir(&d).then_some(d)
}

/// Import a pack directory into `%APPDATA%\WLBS\packs\<name>` (name defaults to the source basename).
/// Refuses a source that is not a loadable pack and a name that would escape the packs dir. The
/// pack is staged in a temp sibling and validated first, then swapped in (so a re-import can't leave
/// stale WAVs behind and a failed one leaves the existing pack untouched), and nested files are
/// copied (BUG-26). Returns the destination.
pub fn import_pack(src: &Path, name: Option<&str>) -> Result<PathBuf, String> {
    if !is_pack_dir(src) {
        return Err(format!("{} is not a loadable BRIR pack (needs the full baked WAV set)", src.display()));
    }
    let name = name
        .map(str::to_string)
        .or_else(|| src.file_name().map(|s| s.to_string_lossy().into_owned()))
        .ok_or_else(|| "cannot determine pack name".to_string())?;
    if !valid_pack_name(&name) {
        return Err(format!("invalid pack name {name:?} (no path separators, '..', or drive colon)"));
    }
    let packs = user_packs_dir();
    let dst = packs.join(&name);
    // Refuse importing a pack onto itself (or a nested path): swapping the destination out would
    // otherwise delete the source. Only possible when `dst` already exists (so we can canonicalize).
    if dst.exists() {
        let src_c = src.canonicalize().map_err(|e| format!("resolve {}: {e}", src.display()))?;
        let dst_c = dst.canonicalize().map_err(|e| format!("resolve {}: {e}", dst.display()))?;
        if src_c == dst_c || src_c.starts_with(&dst_c) || dst_c.starts_with(&src_c) {
            return Err(format!("refusing to import {} onto itself", dst.display()));
        }
    }
    // Copy into a temp sibling and validate it BEFORE touching the existing pack, so a failed or
    // incomplete import never destroys what the user already has.
    let tmp = packs.join(format!("{name}.tmp-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    let staged = std::fs::create_dir_all(&tmp)
        .map_err(|e| format!("create {}: {e}", tmp.display()))
        .and_then(|_| copy_dir_recursive(src, &tmp))
        .and_then(|_| {
            if is_pack_dir(&tmp) {
                Ok(())
            } else {
                Err(format!("import produced an incomplete pack from {} (missing WAVs?)", src.display()))
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

    /// Serialises the tests that point `APPDATA` at a temp dir (process-global env).
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!("wlbs_packs_test_{}_{}", std::process::id(), rand_suffix()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }
    fn rand_suffix() -> u64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().subsec_nanos() as u64
    }

    /// Write the full baked 7.1 WAV set (`<ch>_L.wav`/`<ch>_R.wav`) that `BrirPack::load` reads.
    fn write_baked_set(dir: &Path) {
        std::fs::create_dir_all(dir).unwrap();
        for ch in crate::geometry::CHANNELS_7P1 {
            std::fs::write(dir.join(format!("{ch}_L.wav")), b"RIFF....WAVE").unwrap();
            std::fs::write(dir.join(format!("{ch}_R.wav")), b"RIFF....WAVE").unwrap();
        }
    }

    #[test]
    fn requires_the_full_baked_set() {
        let root = tmp();
        let partial = root.join("partial");
        std::fs::create_dir_all(&partial).unwrap();
        for f in ["L_L.wav", "L_R.wav", "C_L.wav"] {
            std::fs::write(partial.join(f), b"RIFF....WAVE").unwrap();
        }
        assert!(!is_pack_dir(&partial), "a partial WAV set must not count as a pack (BUG-16)");
        let full = root.join("full");
        write_baked_set(&full);
        assert!(is_pack_dir(&full));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn detects_imports_and_clears_on_reimport() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let src = root.join("mypack");
        write_baked_set(&src);
        assert!(is_pack_dir(&src));

        // point APPDATA at our tmp so user_packs_dir() lands there
        std::env::set_var("APPDATA", root.join("appdata"));
        let dst = import_pack(&src, Some("imported")).unwrap();
        assert!(dst.ends_with("WLBS/packs/imported") || dst.ends_with("WLBS\\packs\\imported"));
        assert!(dst.join("L_L.wav").is_file());
        assert_eq!(resolve_pack("imported"), Some(dst.clone()));

        // a stale file in the destination is cleared on re-import (BUG-26)
        std::fs::write(dst.join("STALE.wav"), b"old").unwrap();
        let dst2 = import_pack(&src, Some("imported")).unwrap();
        assert_eq!(dst2, dst);
        assert!(!dst.join("STALE.wav").exists(), "re-import must clear stale files");
        assert!(dst.join("SR_R.wav").is_file());

        // importing a pack onto itself must be refused, not delete it (clear-before-copy guard)
        assert!(import_pack(&dst, Some("imported")).is_err());
        assert!(is_pack_dir(&dst), "a refused self-import must leave the pack intact");

        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn rejects_traversal_names() {
        let root = tmp();
        let src = root.join("mypack");
        write_baked_set(&src); // a valid pack, so the name check is what fails
        for bad in ["../evil", "a/b", "..", ".", "C:evil", ""] {
            assert!(import_pack(&src, Some(bad)).is_err(), "name {bad:?} must be refused");
        }
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn reserved_and_traversal_names_do_not_resolve_or_import() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let src = root.join("mypack");
        write_baked_set(&src);
        std::env::set_var("APPDATA", root.join("appdata2"));
        assert!(import_pack(&src, Some("default")).is_err(), "`default` is reserved");
        assert_eq!(resolve_pack("../mypack"), None);
        assert_eq!(resolve_pack(src.to_str().unwrap()), None, "absolute path must not resolve");
        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_source_is_refused_and_existing_pack_survives() {
        let _env = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let root = tmp();
        let good = root.join("good");
        write_baked_set(&good);
        std::env::set_var("APPDATA", root.join("appdata3"));
        let dst = import_pack(&good, Some("keep")).unwrap();

        // A source whose WAVs are symlinks must be refused up front, leaving `keep` intact.
        let linked = root.join("linked");
        std::fs::create_dir_all(&linked).unwrap();
        for ch in crate::geometry::CHANNELS_7P1 {
            for side in ["L", "R"] {
                std::os::unix::fs::symlink(good.join(format!("{ch}_{side}.wav")), linked.join(format!("{ch}_{side}.wav"))).unwrap();
            }
        }
        assert!(import_pack(&linked, Some("keep")).is_err());
        assert!(is_pack_dir(&dst), "a refused re-import must leave the existing pack intact");
        let leftovers = std::fs::read_dir(user_packs_dir()).unwrap().count();
        assert_eq!(leftovers, 1, "no temp dir left behind");

        std::env::remove_var("APPDATA");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn non_pack_import_refused() {
        let root = tmp();
        let src = root.join("notapack");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join("readme.txt"), b"hi").unwrap();
        assert!(import_pack(&src, None).is_err());
        let _ = std::fs::remove_dir_all(&root);
    }
}
