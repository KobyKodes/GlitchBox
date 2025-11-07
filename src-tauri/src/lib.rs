use std::process::{Command, Child};
use std::sync::Mutex;
use tauri::Manager;

struct BackendProcess(Mutex<Option<Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // Start the Python backend automatically
      let backend_path = app.path().resource_dir()
        .expect("failed to get resource dir")
        .join("movie_api.py");

      println!("Starting backend at: {:?}", backend_path);

      // Try python3 first, fallback to python
      let python_cmd = if Command::new("python3").arg("--version").output().is_ok() {
        "python3"
      } else {
        "python"
      };

      match Command::new(python_cmd)
        .arg(backend_path)
        .spawn() {
          Ok(child) => {
            println!("Backend started successfully with PID: {}", child.id());
            app.manage(BackendProcess(Mutex::new(Some(child))));
          },
          Err(e) => {
            eprintln!("Failed to start backend: {}", e);
            // Continue anyway - user can start backend manually
          }
        }

      Ok(())
    })
    .on_window_event(|_window, event| {
      if let tauri::WindowEvent::Destroyed = event {
        // Backend will be cleaned up when app exits
        println!("Window destroyed");
      }
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
