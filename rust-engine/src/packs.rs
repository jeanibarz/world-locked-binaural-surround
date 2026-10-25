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

/// Does `dir` look like a BRIR pack? (baked WAVs or a filterpack manifest)
pub fn is_pack_dir(dir: &Path) -> bool {
    dir.is_dir()
        && (dir.join("filterpack.json").is_file()
            || dir.join("L_L.wav").is_file()
            || dir.join("C_L.wav").is_file())
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
    let d = user_packs_dir().join(name);
    is_pack_dir(&d).then_some(d)
}

/// Import a pack directory into `%APPDATA%\WLBS\packs\<name>` (name defaults to the source basename).
/// Copies the pack's files (flat); refuses a source that is not a pack. Returns the destination.
pub fn import_pack(src: &Path, name: Option<&str>) -> Result<PathBuf, String> {
    if !is_pack_dir(src) {
        return Err(format!("{} is not a BRIR pack (no WAVs / filterpack.json)", src.display()));
    }
    let name = name
        .map(str::to_string)
        .or_else(|| src.file_name().map(|s| s.to_string_lossy().into_owned()))
        .ok_or_else(|| "cannot determine pack name".to_string())?;
    let dst = user_packs_dir().join(&name);
    std::fs::create_dir_all(&dst).map_err(|e| format!("create {}: {e}", dst.display()))?;
    for entry in std::fs::read_dir(src).map_err(|e| e.to_string())? {
        let p = entry.map_err(|e| e.to_string())?.path();
        if p.is_file() {
            let to = dst.join(p.file_name().unwrap());
            std::fs::copy(&p, &to).map_err(|e| format!("copy {}: {e}", p.display()))?;
        }
    }
    Ok(dst)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!("wlbs_packs_test_{}_{}", std::process::id(), rand_suffix()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }
    fn rand_suffix() -> u64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().subsec_nanos() as u64
    }

    #[test]
    fn detects_and_imports_a_pack() {
        let root = tmp();
        let src = root.join("mypack");
        std::fs::create_dir_all(&src).unwrap();
        for f in ["L_L.wav", "L_R.wav", "C_L.wav"] {
            std::fs::write(src.join(f), b"RIFF....WAVE").unwrap();
        }
        assert!(is_pack_dir(&src));

        // point APPDATA at our tmp so user_packs_dir() lands there
        std::env::set_var("APPDATA", root.join("appdata"));
        let dst = import_pack(&src, Some("imported")).unwrap();
        assert!(dst.ends_with("WLBS/packs/imported") || dst.ends_with("WLBS\\packs\\imported"));
        assert!(dst.join("L_L.wav").is_file());
        assert_eq!(resolve_pack("imported"), Some(dst));
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
