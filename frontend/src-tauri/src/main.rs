#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use serde::Deserialize;
use tauri::{path::BaseDirectory, AppHandle, Manager};

const STORY_SEED_VERSION: u32 = 1;
const STORY_SEED_MARKER: &str = ".loom-story-seed.json";

/// The bundle contains an intentionally empty, versioned data seed. It exists
/// only to make a fresh app-data directory usable; it is never a source of
/// truth after first launch and it never contains a writer's Story database.
#[derive(Deserialize)]
struct StorySeedManifest {
    version: u32,
}

fn story_root(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(root) = std::env::var_os("LOOM_ROOT") {
        return Ok(PathBuf::from(root));
    }

    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
    }

    let root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Could not resolve Loom Story data directory: {error}"))?;
    bootstrap_release_story_data(app, &root)?;
    Ok(root)
}

/// Seed only files that a blank local Story database needs to start. In
/// particular, do not recursively copy `configs/`, a Story DB, or any assets:
/// those may be user-authored and must survive upgrades untouched.
fn bootstrap_release_story_data(app: &AppHandle, root: &Path) -> Result<(), String> {
    // Resolve through Tauri's resource map rather than calculating a bundle
    // path ourselves; debug and release resource layouts differ by platform.
    let manifest = app
        .path()
        .resolve("story-seed/manifest.json", BaseDirectory::Resource)
        .map_err(|error| format!("Could not resolve Loom Story seed resources: {error}"))?;
    let seed_root = manifest
        .parent()
        .ok_or_else(|| "Could not resolve Loom Story seed resources".to_owned())?;
    bootstrap_story_data(root, &seed_root)
}

fn bootstrap_story_data(root: &Path, seed_root: &Path) -> Result<(), String> {
    let manifest_bytes = fs::read(seed_root.join("manifest.json"))
        .map_err(|_| "The packaged Story seed is unavailable".to_owned())?;
    let manifest: StorySeedManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|_| "The packaged Story seed is invalid".to_owned())?;
    if manifest.version != STORY_SEED_VERSION {
        return Err("The packaged Story seed version is unsupported".to_owned());
    }

    let configs = root.join("configs");
    fs::create_dir_all(configs.join("characters"))
        .map_err(|_| "Could not create the local Story character directory".to_owned())?;
    fs::create_dir_all(configs.join("stories"))
        .map_err(|_| "Could not create the local Story database directory".to_owned())?;
    copy_seed_file_if_missing(
        &seed_root.join("configs").join("models.yaml"),
        &configs.join("models.yaml"),
    )?;

    // This marker is diagnostic only. Missing files are checked on every
    // launch, so a crash during a first-run copy can be repaired without ever
    // replacing user data. `create_new` ensures an upgrade never rewrites it.
    let marker = root.join(STORY_SEED_MARKER);
    if !marker.exists() {
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&marker)
        {
            Ok(mut file) => file
                .write_all(format!("{{\"version\":{STORY_SEED_VERSION}}}\n").as_bytes())
                .map_err(|_| "Could not initialize local Story data".to_owned())?,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err("Could not initialize local Story data".to_owned()),
        }
    }
    Ok(())
}

fn copy_seed_file_if_missing(source: &Path, destination: &Path) -> Result<(), String> {
    if destination.exists() {
        return Ok(());
    }
    let parent = destination
        .parent()
        .ok_or_else(|| "Could not initialize local Story data".to_owned())?;
    fs::create_dir_all(parent).map_err(|_| "Could not initialize local Story data".to_owned())?;
    // Read then create-new avoids overwriting a file created by a concurrent
    // first launch. The seed itself is shipped with the application.
    let contents =
        fs::read(source).map_err(|_| "The packaged Story seed is unavailable".to_owned())?;
    let mut file = match OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
    {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => return Ok(()),
        Err(_) => return Err("Could not initialize local Story data".to_owned()),
    };
    file.write_all(&contents)
        .map_err(|_| "Could not initialize local Story data".to_owned())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn minimal_seed_creates_only_missing_story_config() {
        let unique = format!(
            "loom-story-seed-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        );
        let base = std::env::temp_dir().join(unique);
        let seed = base.join("seed");
        let root = base.join("data");
        fs::create_dir_all(seed.join("configs")).expect("seed dirs");
        fs::write(seed.join("manifest.json"), "{\"version\":1}").expect("manifest");
        fs::write(seed.join("configs").join("models.yaml"), "models: {}\n").expect("seed models");

        bootstrap_story_data(&root, &seed).expect("first bootstrap");
        assert_eq!(
            fs::read_to_string(root.join("configs").join("models.yaml")).unwrap(),
            "models: {}\n"
        );
        assert!(root.join("configs").join("characters").is_dir());
        assert!(root.join("configs").join("stories").is_dir());

        fs::write(
            root.join("configs").join("models.yaml"),
            "models: { user: {} }\n",
        )
        .expect("user config");
        bootstrap_story_data(&root, &seed).expect("repeat bootstrap");
        assert_eq!(
            fs::read_to_string(root.join("configs").join("models.yaml")).unwrap(),
            "models: { user: {} }\n"
        );

        fs::remove_dir_all(base).expect("cleanup");
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            // First-run data seeding for the packaged shell. Debug builds keep
            // using the workspace root, which already holds the Story data.
            story_root(app.handle())?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![])
        .run(tauri::generate_context!())
        .expect("error while running Loom Story desktop app");
}
