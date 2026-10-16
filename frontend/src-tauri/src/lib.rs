mod terminal;
use std::sync::atomic::Ordering;
use tauri::Manager;
use terminal::DesktopState;

pub fn run() {
    let application = tauri::Builder::default()
        .manage(DesktopState::default())
        .invoke_handler(tauri::generate_handler![
            terminal::terminal_start,
            terminal::terminal_connection,
            terminal::terminal_subscribe,
            terminal::terminal_unsubscribe,
            terminal::terminal_resource_recover,
            terminal::terminal_snapshot,
            terminal::terminal_action,
            terminal::terminal_watchlist,
            terminal::terminal_chart,
            terminal::terminal_browser,
            terminal::terminal_watchlist_preview,
            terminal::terminal_resource,
            terminal::terminal_service,
            terminal::terminal_research
        ])
        .build(tauri::generate_context!());
    match application {
        Ok(application) => application.run(|app, event| {
            if let tauri::RunEvent::ExitRequested { api, code, .. } = event {
                let state = app.state::<DesktopState>();
                if !state.finished.load(Ordering::Acquire) {
                    api.prevent_exit();
                    if state
                        .exiting
                        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                        .is_ok()
                    {
                        state.cancel_startup.notify_waiters();
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let state = app.state::<DesktopState>();
                            match tokio::time::timeout(
                                std::time::Duration::from_secs(12),
                                state.shutdown(),
                            )
                            .await
                            {
                                Ok(Ok(())) => {}
                                Ok(Err(error)) => eprintln!("Terminal shutdown: {error}"),
                                Err(_) => eprintln!("Terminal shutdown exceeded twelve seconds"),
                            }
                            state.finished.store(true, Ordering::Release);
                            app.exit(code.unwrap_or(0));
                        });
                    }
                }
            }
        }),
        Err(error) => {
            eprintln!("Desktop startup failed: {error}");
            std::process::exit(1);
        }
    }
}
