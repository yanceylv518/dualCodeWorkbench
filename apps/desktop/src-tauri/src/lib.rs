use std::sync::Mutex;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct BackendProcess(Mutex<Option<CommandChild>>);
struct SidecarToken(String);

#[tauri::command]
fn backend_url() -> &'static str {
    "http://127.0.0.1:8876"
}

#[tauri::command]
fn sidecar_token(token: tauri::State<'_, SidecarToken>) -> String {
    token.0.clone()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![backend_url, sidecar_token])
        .setup(|app| {
            let parent_pid = std::process::id().to_string();
            let token = format!("{}{}", uuid::Uuid::new_v4(), uuid::Uuid::new_v4());
            let (_, child) = app
                .shell()
                .sidecar("dualcode-backend")?
                .args(["--parent-pid", &parent_pid])
                .env("DUALCODE_SIDECAR_TOKEN", &token)
                .spawn()?;
            app.manage(BackendProcess(Mutex::new(Some(child))));
            app.manage(SidecarToken(token));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building DualCode Workbench");

    app.run(|app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            if let Some(state) = app_handle.try_state::<BackendProcess>() {
                if let Ok(mut child) = state.0.lock() {
                    if let Some(process) = child.take() {
                        let _ = process.kill();
                    }
                }
            }
        }
    });
}
