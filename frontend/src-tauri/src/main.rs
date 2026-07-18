#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use serde::Deserialize;
use serde_json::Value;
use tauri::{path::BaseDirectory, AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};
use tokio::sync::oneshot;

const STORY_EVENT: &str = "story-event";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const MAX_STORY_IMAGE_BYTES: u64 = 8 * 1024 * 1024;
const PNG_SIGNATURE: &[u8; 8] = b"\x89PNG\r\n\x1a\n";
const STORY_ASSET_UNAVAILABLE: &str = "Story image is unavailable";
const STORY_SEED_VERSION: u32 = 1;
const STORY_SEED_MARKER: &str = ".loom-story-seed.json";

type PendingResponse = oneshot::Sender<Result<Value, String>>;
type PendingRequests = Arc<Mutex<HashMap<String, PendingResponse>>>;

/// Long-lived JSONL process state. The Story Host is intentionally not an HTTP
/// service: all traffic flows through its stdin/stdout pipes.
#[derive(Clone)]
struct StoryHostState {
    child: Arc<Mutex<Option<CommandChild>>>,
    pending: PendingRequests,
    stdout_buffer: Arc<Mutex<Vec<u8>>>,
}

/// Opaque descriptor returned by the Story Host for a generated Story image.
/// It is deliberately much narrower than an arbitrary filesystem path.
#[derive(Deserialize)]
struct StoryAssetDescriptor {
    story_key: String,
    image_name: String,
    media_type: String,
}

/// The bundle contains an intentionally empty, versioned data seed. It exists
/// only to make a fresh app-data directory usable; it is never a source of
/// truth after first launch and it never contains a writer's Story database.
#[derive(Deserialize)]
struct StorySeedManifest {
    version: u32,
}

impl Default for StoryHostState {
    fn default() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            pending: Arc::new(Mutex::new(HashMap::new())),
            stdout_buffer: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

#[tauri::command]
async fn story_request(state: State<'_, StoryHostState>, request: Value) -> Result<Value, String> {
    let request_id = request
        .get("id")
        .and_then(Value::as_str)
        .filter(|id| !id.trim().is_empty())
        .ok_or_else(|| "Story Host request requires a non-empty string id".to_owned())?
        .to_owned();

    let line = serde_json::to_string(&request)
        .map_err(|error| format!("Could not encode Story Host request: {error}"))?;
    let (sender, receiver) = oneshot::channel();

    {
        let mut pending = state
            .pending
            .lock()
            .map_err(|_| "Story Host pending-request lock was poisoned".to_owned())?;
        if pending.contains_key(&request_id) {
            return Err(format!(
                "A Story Host request is already using id '{request_id}'"
            ));
        }
        pending.insert(request_id.clone(), sender);
    }

    // Do not use `?` directly here: the request was already added to the
    // pending map and every write failure must remove it before returning.
    let write_result: Result<(), String> = (|| {
        let mut child = state
            .child
            .lock()
            .map_err(|_| "Story Host child-process lock was poisoned".to_owned())?;
        let child = child
            .as_mut()
            .ok_or_else(|| "Story Host is not running".to_owned())?;
        child
            .write(format!("{line}\n").as_bytes())
            .map_err(|error| format!("Could not write to Story Host: {error}"))
    })();

    if let Err(error) = write_result {
        remove_pending(&state.pending, &request_id);
        return Err(error);
    }

    match tokio::time::timeout(REQUEST_TIMEOUT, receiver).await {
        Ok(Ok(Ok(response))) => Ok(response),
        Ok(Ok(Err(error))) => Err(error),
        Ok(Err(_)) => Err("Story Host response channel closed unexpectedly".to_owned()),
        Err(_) => {
            remove_pending(&state.pending, &request_id);
            Err("Story Host did not respond before the request timeout".to_owned())
        }
    }
}

/// Read a persisted Story image through Tauri IPC instead of exposing a local
/// HTTP file endpoint. Every part of the descriptor is validated before any
/// file is opened, and canonical paths must remain direct children of the
/// selected Story's image directory.
#[tauri::command]
fn story_asset_data_url(app: AppHandle, asset: StoryAssetDescriptor) -> Result<String, String> {
    let root = story_root(&app).map_err(|_| STORY_ASSET_UNAVAILABLE.to_owned())?;
    read_story_asset_data_url(&root, &asset).map_err(|_| STORY_ASSET_UNAVAILABLE.to_owned())
}

fn read_story_asset_data_url(root: &Path, asset: &StoryAssetDescriptor) -> Result<String, ()> {
    let safe_story_key = safe_story_segment(&asset.story_key).ok_or(())?;
    if asset.media_type != "image/png" || !is_safe_png_name(&asset.image_name) {
        return Err(());
    }

    // Canonicalize every directory boundary. This rejects symlink escapes as
    // well as ordinary traversal, even if a hostile file is placed beneath an
    // otherwise valid Story folder.
    let canonical_root = root.canonicalize().map_err(|_| ())?;
    let canonical_story_base = canonical_root
        .join("configs")
        .join("stories")
        .canonicalize()
        .map_err(|_| ())?;
    if !canonical_story_base.is_dir() || !canonical_story_base.starts_with(&canonical_root) {
        return Err(());
    }

    let canonical_story_directory = canonical_direct_child(&canonical_story_base, &safe_story_key)?;
    if !canonical_story_directory.is_dir() {
        return Err(());
    }
    let canonical_images_directory = canonical_direct_child(&canonical_story_directory, "images")?;
    if !canonical_images_directory.is_dir() {
        return Err(());
    }
    let canonical_image = canonical_direct_child(&canonical_images_directory, &asset.image_name)?;

    let file = File::open(&canonical_image).map_err(|_| ())?;
    let metadata = file.metadata().map_err(|_| ())?;
    if !metadata.is_file() || metadata.len() > MAX_STORY_IMAGE_BYTES {
        return Err(());
    }

    // Bound the actual read as well as metadata: a concurrent replacement
    // cannot turn this command into an unbounded memory allocation.
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    let mut limited_file = file.take(MAX_STORY_IMAGE_BYTES + 1);
    limited_file.read_to_end(&mut bytes).map_err(|_| ())?;
    if bytes.len() as u64 > MAX_STORY_IMAGE_BYTES || !has_png_signature(&bytes) {
        return Err(());
    }

    Ok(format!(
        "data:image/png;base64,{}",
        BASE64_STANDARD.encode(bytes)
    ))
}

/// Resolve exactly one physical direct child after canonicalization. A normal
/// `starts_with` check would still accept a symlinked image directory or file;
/// requiring the same parent and file name closes that route.
fn canonical_direct_child(parent: &Path, child_name: &str) -> Result<PathBuf, ()> {
    let canonical_child = parent.join(child_name).canonicalize().map_err(|_| ())?;
    if canonical_child.parent() != Some(parent)
        || canonical_child.file_name().and_then(|name| name.to_str()) != Some(child_name)
    {
        return Err(());
    }
    Ok(canonical_child)
}

/// Match the existing lean image directory convention while refusing strings
/// that could express a path. The normalization only applies to a known Story
/// key from the host; it never accepts a fallback directory.
fn safe_story_segment(value: &str) -> Option<String> {
    let raw = value.trim();
    if raw.is_empty()
        || raw == "."
        || raw == ".."
        || raw.contains('/')
        || raw.contains('\\')
        || raw.contains('\0')
    {
        return None;
    }

    let mut normalized = String::with_capacity(raw.len());
    let mut replacing_disallowed = false;
    for character in raw.chars() {
        if character.is_ascii_alphanumeric() || character == '_' || character == '-' {
            normalized.push(character);
            replacing_disallowed = false;
        } else if !replacing_disallowed {
            // Python's `re.sub(...+)` turns a run of unsupported characters
            // into one dash; keep this IPC resolver aligned with persisted
            // Story image folders.
            normalized.push('-');
            replacing_disallowed = true;
        }
    }

    let normalized = normalized.trim_matches(|character| character == '-' || character == '_');
    (!normalized.is_empty()).then(|| normalized.to_owned())
}

fn is_safe_png_name(name: &str) -> bool {
    if name.is_empty()
        || name.contains('/')
        || name.contains('\\')
        || name.contains('\0')
        || name.contains("..")
        || !name.ends_with(".png")
    {
        return false;
    }

    let stem = &name[..name.len() - ".png".len()];
    !stem.is_empty()
        && stem
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

fn has_png_signature(bytes: &[u8]) -> bool {
    bytes.starts_with(PNG_SIGNATURE)
}

fn start_story_host(app: &AppHandle, state: StoryHostState) -> Result<(), String> {
    let root = story_root(app)?;
    let root = root.to_string_lossy().into_owned();
    let command = app
        .shell()
        .sidecar("story-host")
        .map_err(|error| format!("Could not resolve Story Host sidecar: {error}"))?
        .args(["--root", root.as_str()]);
    let (mut receiver, child) = command
        .spawn()
        .map_err(|error| format!("Could not start Story Host sidecar: {error}"))?;

    {
        let mut slot = state
            .child
            .lock()
            .map_err(|_| "Story Host child-process lock was poisoned".to_owned())?;
        *slot = Some(child);
    }

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => route_story_host_stdout(&app, &state, &bytes),
                CommandEvent::Stderr(_) => {
                    // Sidecar stderr is diagnostic-only. It never enters the story-event
                    // stream, which stays reserved for validated protocol messages.
                }
                CommandEvent::Error(error) => {
                    mark_sidecar_stopped(&state);
                    reject_all_pending(&state.pending, format!("Story Host pipe error: {error}"));
                }
                CommandEvent::Terminated(status) => {
                    mark_sidecar_stopped(&state);
                    reject_all_pending(
                        &state.pending,
                        format!("Story Host stopped (exit code: {:?})", status.code),
                    );
                }
                _ => {}
            }
        }
    });

    Ok(())
}

fn route_story_host_stdout(app: &AppHandle, state: &StoryHostState, bytes: &[u8]) {
    // CommandEvent::Stdout is a byte chunk, not a JSONL-record boundary. Keep
    // partial UTF-8/JSON records until their newline arrives; a streamed OMP
    // event may otherwise be split or several responses may arrive together.
    let lines = match drain_jsonl_lines(&state.stdout_buffer, bytes) {
        Ok(lines) => lines,
        Err(error) => {
            eprintln!("Ignored malformed Story Host JSONL bytes: {error}");
            return;
        }
    };

    for line in lines {
        route_story_host_message(app, state, line);
    }
}

fn drain_jsonl_lines(buffer: &Arc<Mutex<Vec<u8>>>, bytes: &[u8]) -> Result<Vec<String>, String> {
    let mut buffer = buffer
        .lock()
        .map_err(|_| "Story Host stdout buffer lock was poisoned".to_owned())?;
    buffer.extend_from_slice(bytes);
    let mut lines = Vec::new();
    while let Some(end) = buffer.iter().position(|byte| *byte == b'\n') {
        let raw = buffer.drain(..=end).collect::<Vec<_>>();
        let line = std::str::from_utf8(&raw[..raw.len().saturating_sub(1)])
            .map_err(|error| format!("invalid UTF-8: {error}"))?
            .trim_end_matches('\r')
            .to_owned();
        if !line.trim().is_empty() {
            lines.push(line);
        }
    }
    Ok(lines)
}

fn route_story_host_message(app: &AppHandle, state: &StoryHostState, line: String) {
    let message: Value = match serde_json::from_str(&line) {
        Ok(message) => message,
        Err(error) => {
            eprintln!("Ignored malformed Story Host JSONL message: {error}");
            return;
        }
    };

    if message.get("type").and_then(Value::as_str) == Some("response") {
        let Some(request_id) = message.get("id").and_then(Value::as_str) else {
            eprintln!("Ignored Story Host response without an id");
            return;
        };

        let sender = state
            .pending
            .lock()
            .ok()
            .and_then(|mut pending| pending.remove(request_id));
        if let Some(sender) = sender {
            let _ = sender.send(Ok(message));
        } else {
            eprintln!("Ignored Story Host response with no waiting request");
        }
        return;
    }

    if let Err(error) = app.emit(STORY_EVENT, message) {
        eprintln!("Could not emit Story Host event: {error}");
    }
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

fn remove_pending(pending: &PendingRequests, request_id: &str) {
    if let Ok(mut pending) = pending.lock() {
        pending.remove(request_id);
    }
}

fn reject_all_pending(pending: &PendingRequests, message: String) {
    let senders = match pending.lock() {
        Ok(mut pending) => pending
            .drain()
            .map(|(_, sender)| sender)
            .collect::<Vec<_>>(),
        Err(_) => return,
    };

    for sender in senders {
        let _ = sender.send(Err(message.clone()));
    }
}

fn mark_sidecar_stopped(state: &StoryHostState) {
    if let Ok(mut child) = state.child.lock() {
        *child = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn story_asset_descriptor_accepts_only_the_host_png_shape() {
        assert!(is_safe_png_name("scene-7f1dbb.png"));
        assert!(!is_safe_png_name("scene.jpg"));
        assert!(!is_safe_png_name("../scene.png"));
        assert!(!is_safe_png_name("scene\\other.png"));
        assert!(!is_safe_png_name("scene..png"));
    }

    #[test]
    fn story_key_normalization_matches_persisted_image_directories_without_paths() {
        assert_eq!(
            safe_story_segment("  The Dying Light  ").as_deref(),
            Some("The-Dying-Light")
        );
        assert_eq!(
            safe_story_segment("the_dying_light").as_deref(),
            Some("the_dying_light")
        );
        assert!(safe_story_segment("../other-story").is_none());
        assert!(safe_story_segment("..\\other-story").is_none());
        assert!(safe_story_segment("---").is_none());
    }

    #[test]
    fn png_signature_is_required() {
        assert!(has_png_signature(b"\x89PNG\r\n\x1a\nbody"));
        assert!(!has_png_signature(b"not a png"));
    }

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
        .manage(StoryHostState::default())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let state = app.state::<StoryHostState>().inner().clone();
            start_story_host(&app.handle(), state).map_err(std::io::Error::other)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            story_request,
            story_asset_data_url
        ])
        .run(tauri::generate_context!())
        .expect("error while running Loom Story desktop app");
}
