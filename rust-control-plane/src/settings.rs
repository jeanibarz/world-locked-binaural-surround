//! Crash-safe settings store: a JSON file written atomically (temp + rename), so a crash or power
//! loss mid-write can never leave a half-written/corrupt settings file — the engine always loads
//! either the previous good settings or the new ones, never garbage. Mirrors the Python
//! `config_store` durability contract.

use std::path::{Path, PathBuf};

/// A JSON settings file persisted atomically.
pub struct SettingsStore {
    path: PathBuf,
}

impl SettingsStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    /// Load the settings, or `Value::Null` if the file is absent/unreadable/corrupt (a corrupt file
    /// never crashes the engine — it falls back to defaults, like the Python store).
    pub fn load(&self) -> serde_json::Value {
        match std::fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null),
            Err(_) => serde_json::Value::Null,
        }
    }

    /// Atomically persist `value`: write to a sibling temp file, fsync, then rename over the target
    /// (rename is atomic on the same filesystem), so readers see all-or-nothing.
    pub fn save(&self, value: &serde_json::Value) -> std::io::Result<()> {
        use std::io::Write;
        let dir = self.path.parent().unwrap_or_else(|| Path::new("."));
        std::fs::create_dir_all(dir)?;
        let tmp = self.path.with_extension("json.tmp");
        {
            let mut f = std::fs::File::create(&tmp)?;
            f.write_all(&serde_json::to_vec_pretty(value)?)?;
            f.sync_all()?; // flush to disk before the rename, so the rename can't expose a short file
        }
        std::fs::rename(&tmp, &self.path)?;
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
        assert!(!path.with_extension("json.tmp").exists());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_file_loads_as_null_not_a_crash() {
        let dir = std::env::temp_dir().join(format!("wlbs_settings_corrupt_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        std::fs::write(&path, b"{ this is not json").unwrap();
        assert_eq!(SettingsStore::new(&path).load(), serde_json::Value::Null);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
