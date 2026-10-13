use std::io::Read;
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;

const IMPORT_LIMIT: u64 = 1_048_576;

fn workspace_route(module: &str) -> Result<&'static str, String> {
    match module {
        "portfolio" => Ok("portfolio"),
        "research" => Ok("research"),
        _ => Err("Workspace is not available".into()),
    }
}

#[tauri::command]
fn open_workspace(app: tauri::AppHandle, module: String) -> Result<(), String> {
    let route = workspace_route(&module)?;
    let label = format!("workspace-{route}");
    if let Some(window) = app.get_webview_window(&label) {
        return window
            .set_focus()
            .map_err(|_| "Cannot focus workspace".into());
    }
    tauri::WebviewWindowBuilder::new(&app, label, tauri::WebviewUrl::App(route.into()))
        .title(format!("Financial Terminal — {module}"))
        .inner_size(1440.0, 1000.0)
        .build()
        .map(|_| ())
        .map_err(|_| "Cannot open workspace window".into())
}

fn read_import(path: &std::path::Path) -> Result<String, String> {
    let file = std::fs::File::open(path).map_err(|_| "Cannot open selected file")?;
    if !file
        .metadata()
        .map_err(|_| "Cannot inspect selected file")?
        .is_file()
    {
        return Err("Select a regular JSON file".into());
    }
    if file
        .metadata()
        .map_err(|_| "Cannot inspect selected file")?
        .len()
        > IMPORT_LIMIT
    {
        return Err("Portfolio import exceeds 1 MiB".into());
    }
    let mut content = String::new();
    file.take(IMPORT_LIMIT + 1)
        .read_to_string(&mut content)
        .map_err(|_| "Selected file must contain UTF-8 text")?;
    if content.len() as u64 > IMPORT_LIMIT {
        return Err("Portfolio import exceeds 1 MiB".into());
    }
    let _: serde_json::Value =
        serde_json::from_str(&content).map_err(|_| "Selected file must contain valid JSON")?;
    Ok(content)
}

#[tauri::command]
async fn open_portfolio_file(app: tauri::AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let chosen = app
            .dialog()
            .file()
            .add_filter("Portfolio JSON", &["json"])
            .blocking_pick_file();
        match chosen {
            Some(file) => {
                read_import(&file.into_path().map_err(|_| "Select a local file")?).map(Some)
            }
            None => Ok(None),
        }
    })
    .await
    .map_err(|_| "File selection failed".to_string())?
}

pub fn run() {
    if let Err(error) = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            open_workspace,
            open_portfolio_file
        ])
        .run(tauri::generate_context!())
    {
        eprintln!("Desktop startup failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn workspace_routes_are_allowlisted() {
        assert!(workspace_route("portfolio").is_ok());
        assert!(workspace_route("https://remote.example").is_err());
        assert!(workspace_route("../../etc/passwd").is_err());
    }
    #[test]
    fn selected_import_is_bounded_utf8_json() {
        use std::io::Write;
        let mut file = tempfile::NamedTempFile::new().unwrap_or_else(|e| panic!("fixture: {e}"));
        file.write_all(b"{\"portfolio_name\":\"Saved\"}")
            .unwrap_or_else(|e| panic!("fixture: {e}"));
        assert!(read_import(file.path()).is_ok());
        file.as_file_mut()
            .set_len(IMPORT_LIMIT + 1)
            .unwrap_or_else(|e| panic!("fixture: {e}"));
        assert!(matches!(read_import(file.path()), Err(error) if error.contains("1 MiB")));
        file.as_file_mut()
            .set_len(0)
            .unwrap_or_else(|e| panic!("fixture: {e}"));
        assert!(read_import(file.path()).is_err());
    }
}
