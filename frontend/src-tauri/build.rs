fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["open_workspace", "open_portfolio_file"]),
    ))
    .unwrap_or_else(|error| panic!("Desktop build failed: {error}"));
}
