//! Crash-safe settings store: a JSON file written atomically (temp + rename), so a crash or power
//! loss mid-write can never leave a half-written/corrupt settings file — the engine always loads
//! either the previous good settings or the new ones, never garbage. Mirrors the Python
//! `config_store` durability contract.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

/// Distinguishes temp files of overlapping saves within one process (the pid covers processes).
static TMP_SEQ: AtomicU64 = AtomicU64::new(0);

/// A JSON settings file persisted atomically.
pub struct SettingsStore {
    path: PathBuf,
    save_lock: Mutex<()>,
}

impl SettingsStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into(), save_lock: Mutex::new(()) }
    }

    /// Load the settings, or `Value::Null` if the file is absent/unreadable/corrupt (a corrupt file
    /// never crashes the engine — it falls back to defaults, like the Python store).
    pub fn load(&self) -> serde_json::Value {
        match std::fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|_| {
                // Keep the unreadable file (best effort) so the next save doesn't destroy it.
                let _ = std::fs::write(self.path.with_extension("json.bak"), &bytes);
                serde_json::Value::Null
            }),
            Err(_) => serde_json::Value::Null,
        }
    }

    /// Atomically persist `value`: write to a sibling temp file, fsync, then rename over the target
    /// (rename is atomic on the same filesystem), so readers see all-or-nothing.
    pub fn save(&self, value: &serde_json::Value) -> std::io::Result<()> {
        use std::io::Write;
        // Serialize saves in-process; the unique temp name also protects against other processes.
        let _guard = self.save_lock.lock().unwrap_or_else(|e| e.into_inner());
        let dir = self.path.parent().unwrap_or_else(|| Path::new("."));
        std::fs::create_dir_all(dir)?;
        let tmp = self.path.with_extension(format!(
            "json.{}.{}.tmp",
            std::process::id(),
            TMP_SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        let written = (|| -> std::io::Result<()> {
            let mut f = std::fs::File::create(&tmp)?;
            f.write_all(&serde_json::to_vec_pretty(value)?)?;
            f.sync_all()?; // flush to disk before the rename, so the rename can't expose a short file
            std::fs::rename(&tmp, &self.path)
        })();
        if let Err(e) = written {
            let _ = std::fs::remove_file(&tmp);
            return Err(e);
        }
        // Best effort: make the rename itself durable (not possible on every platform).
        if let Ok(d) = std::fs::File::open(dir) {
            let _ = d.sync_all();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_and_overwrites_atomically() {
        let dir = std::env::temp_dir().join(format!("wlbs_settings_test_{}", std::process::id()));
        let path = dir.join("settings.json");
        let store = SettingsStore::new(&path);
        assert_eq!(store.load(), serde_json::Value::Null); // absent -> Null

        let v1 = serde_json::json!({"block": 256, "method": "timealign"});
        store.save(&v1).unwrap();
        assert_eq!(store.load(), v1);

        let v2 = serde_json::json!({"block": 512, "method": "nearest", "extra": true});
        store.save(&v2).unwrap();
        assert_eq!(store.load(), v2); // overwrite
        // no stray temp left behind
        let stray = std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok())
            .any(|e| e.file_name().to_string_lossy().ends_with(".tmp"));
        assert!(!stray);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_file_loads_as_null_not_a_crash() {
        let dir = std::env::temp_dir().join(format!("wlbs_settings_corrupt_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        std::fs::write(&path, b"{ this is not json").unwrap();
        assert_eq!(SettingsStore::new(&path).load(), serde_json::Value::Null);
        assert_eq!(std::fs::read(path.with_extension("json.bak")).unwrap(), b"{ this is not json");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_saves_never_corrupt() {
        let dir = std::env::temp_dir().join(format!("wlbs_settings_conc_{}", std::process::id()));
        let path = dir.join("settings.json");
        let handles: Vec<_> = (0..8)
            .map(|t| {
                let p = path.clone();
                std::thread::spawn(move || {
                    // separate stores: only the unique temp names protect these from each other
                    let store = SettingsStore::new(p);
                    for i in 0..25 {
                        store.save(&serde_json::json!({"t": t, "i": i})).unwrap();
                    }
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap();
        }
        assert!(SettingsStore::new(&path).load().is_object());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
