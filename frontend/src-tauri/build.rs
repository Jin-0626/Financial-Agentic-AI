fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "terminal_start",
            "terminal_connection",
            "terminal_subscribe",
            "terminal_unsubscribe",
            "terminal_resource_recover",
            "terminal_snapshot",
            "terminal_action",
            "terminal_watchlist",
            "terminal_chart",
            "terminal_browser",
            "terminal_watchlist_preview",
            "terminal_resource",
            "terminal_service",
            "terminal_research",
        ]),
    ))
    .unwrap_or_else(|error| panic!("Desktop build failed: {error}"));
}
