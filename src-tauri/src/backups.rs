//! Database backups in `<data>\backups\`: created by the backend before schema migrations
//! (`mustang-schema<N>-*.db`) and by the shell before installing an update
//! (`mustang-pre-update-*.db`); restorable from Settings → Desktop or from the failure
//! screen (e.g. after a downgrade meets a newer database).
//!
//! The backend must be stopped while files are copied (the database is in WAL mode).

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::State;

use crate::backend::BackendManager;
use crate::paths::DataDirs;

const KEEP_PER_KIND: usize = 5;

#[derive(Serialize, Debug)]
pub struct BackupEntry {
    name: String,
    kind: &'static str,
    size: u64,
    /// Seconds since the Unix epoch.
    modified: u64,
}

fn backups_dir(dirs: &DataDirs) -> PathBuf {
    dirs.data.join("backups")
}

fn kind_of(name: &str) -> &'static str {
    if name.starts_with("mustang-pre-update-") {
        "pre-update"
    } else if name.starts_with("mustang-before-restore-") {
        "before-restore"
    } else if name.starts_with("mustang-schema") {
        "schema-migration"
    } else {
        "other"
    }
}

/// Only plain file names of the form `mustang-<safe chars>.db` are accepted (no paths).
pub fn is_valid_backup_name(name: &str) -> bool {
    name.len() <= 120
        && name.starts_with("mustang-")
        && name.ends_with(".db")
        && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        && !name.contains("..")
}

pub fn list(dirs: &DataDirs) -> Vec<BackupEntry> {
    let mut entries: Vec<BackupEntry> = fs::read_dir(backups_dir(dirs))
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let meta = e.metadata().ok()?;
            (meta.is_file() && is_valid_backup_name(&name)).then(|| BackupEntry {
                kind: kind_of(&name),
                size: meta.len(),
                modified: meta.modified().ok().and_then(|m| m.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0),
                name,
            })
        })
        .collect();
    entries.sort_by(|a, b| b.modified.cmp(&a.modified));
    entries
}

fn stamp() -> String {
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    secs.to_string()
}

/// Copy the (stopped) database and its WAL/SHM side files to `dest` (+ "-wal"/"-shm").
fn copy_db(src: &Path, dest: &Path) -> Result<(), String> {
    fs::copy(src, dest).map_err(|e| format!("cannot copy {}: {e}", src.display()))?;
    for side in ["-wal", "-shm"] {
        let s = PathBuf::from(format!("{}{side}", src.display()));
        let d = PathBuf::from(format!("{}{side}", dest.display()));
        if s.exists() {
            fs::copy(&s, &d).map_err(|e| format!("cannot copy {}: {e}", s.display()))?;
        } else {
            let _ = fs::remove_file(&d);
        }
    }
    Ok(())
}

fn prune(dirs: &DataDirs, kind: &'static str) {
    let mut of_kind: Vec<_> = list(dirs).into_iter().filter(|b| b.kind == kind).collect();
    of_kind.sort_by(|a, b| b.modified.cmp(&a.modified));
    for old in of_kind.into_iter().skip(KEEP_PER_KIND) {
        let p = backups_dir(dirs).join(&old.name);
        let _ = fs::remove_file(&p);
        let _ = fs::remove_file(format!("{}-wal", p.display()));
        let _ = fs::remove_file(format!("{}-shm", p.display()));
    }
}

/// Backup before installing an update. The backend must already be stopped.
pub fn backup_before_update(dirs: &DataDirs, from: &str, to: &str) -> Result<Option<PathBuf>, String> {
    let db = dirs.database();
    if !db.exists() {
        return Ok(None);
    }
    fs::create_dir_all(backups_dir(dirs)).map_err(|e| e.to_string())?;
    let safe = |v: &str| v.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '.').collect::<String>();
    let dest = backups_dir(dirs).join(format!("mustang-pre-update-{}-to-{}-{}.db", safe(from), safe(to), stamp()));
    copy_db(&db, &dest)?;
    prune(dirs, "pre-update");
    dirs.log(&format!("pre-update backup written: {}", dest.display()));
    Ok(Some(dest))
}

/// Replace the live database with a backup. The current database is kept as a
/// `mustang-before-restore-*.db` backup first. The backend must already be stopped.
pub fn restore(dirs: &DataDirs, name: &str) -> Result<(), String> {
    if !is_valid_backup_name(name) {
        return Err("Invalid backup name.".into());
    }
    let src = backups_dir(dirs).join(name);
    if !src.is_file() {
        return Err(format!("Backup {name} not found."));
    }
    let db = dirs.database();
    if db.exists() {
        let keep = backups_dir(dirs).join(format!("mustang-before-restore-{}.db", stamp()));
        copy_db(&db, &keep)?;
        dirs.log(&format!("current database kept as {}", keep.display()));
    }
    copy_db(&src, &db)?;
    prune(dirs, "before-restore");
    dirs.log(&format!("database restored from {name}"));
    Ok(())
}

#[tauri::command]
pub fn list_backups(manager: State<'_, Arc<BackendManager>>) -> Vec<BackupEntry> {
    manager.data_dirs().map(|d| list(&d)).unwrap_or_default()
}

/// Stop the backend, restore, start it again.
#[tauri::command(async)]
pub fn restore_backup(manager: State<'_, Arc<BackendManager>>, name: String) -> Result<(), String> {
    let manager = manager.inner().clone();
    let dirs = manager.data_dirs().ok_or("The app is still starting")?;
    manager.stop_for_maintenance();
    let result = restore(&dirs, &name);
    manager.restart();
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dirs(tag: &str) -> DataDirs {
        let root = std::env::temp_dir().join(format!("mustang-backups-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let d = DataDirs { data: root.join("data"), logs: root.join("logs"), default_models: root.join("models"), root };
        fs::create_dir_all(&d.logs).unwrap();
        fs::create_dir_all(d.data.join("backups")).unwrap();
        d
    }

    #[test]
    fn backup_names_are_strictly_validated() {
        assert!(is_valid_backup_name("mustang-schema0-20261102-161123.db"));
        assert!(is_valid_backup_name("mustang-pre-update-0.1.0-to-0.1.1-1793600000.db"));
        for bad in ["..\\mustang.db", "mustang-../../x.db", "C:\\x\\mustang-a.db", "mustang-a.db\\..", "mustang-a.exe", "other.db", "mustang-a b.db"] {
            assert!(!is_valid_backup_name(bad), "{bad}");
        }
    }

    #[test]
    fn pre_update_backup_then_restore_keeps_current_copy() {
        let d = dirs("roundtrip");
        fs::write(d.database(), b"version-A").unwrap();
        fs::write(format!("{}-wal", d.database().display()), b"wal-A").unwrap();
        let backup = backup_before_update(&d, "0.1.0", "0.1.1").unwrap().unwrap();
        assert_eq!(fs::read(&backup).unwrap(), b"version-A");
        assert_eq!(fs::read(format!("{}-wal", backup.display())).unwrap(), b"wal-A");

        fs::write(d.database(), b"version-B").unwrap();
        fs::remove_file(format!("{}-wal", d.database().display())).unwrap();
        let name = backup.file_name().unwrap().to_string_lossy().to_string();
        restore(&d, &name).unwrap();
        assert_eq!(fs::read(d.database()).unwrap(), b"version-A");
        assert_eq!(fs::read(format!("{}-wal", d.database().display())).unwrap(), b"wal-A");
        let kept: Vec<_> = list(&d).into_iter().filter(|b| b.kind == "before-restore").collect();
        assert_eq!(kept.len(), 1);
        assert_eq!(fs::read(d.data.join("backups").join(&kept[0].name)).unwrap(), b"version-B");
        assert!(restore(&d, "mustang-missing.db").is_err());
        let _ = fs::remove_dir_all(&d.root);
    }

    #[test]
    fn no_database_means_no_pre_update_backup() {
        let d = dirs("empty");
        assert!(backup_before_update(&d, "0.1.0", "0.1.1").unwrap().is_none());
        let _ = fs::remove_dir_all(&d.root);
    }
}
