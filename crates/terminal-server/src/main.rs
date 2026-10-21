//! Standalone Rust HTTP entrypoint; legacy services remain available during rollout.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = terminal_server::ServerConfig::from_environment()?;
    let listener = tokio::net::TcpListener::bind(config.address).await?;
    eprintln!("Rust terminal API listening on {}", listener.local_addr()?);
    let shutdown = async {
        if let Err(error) = tokio::signal::ctrl_c().await {
            eprintln!("Unable to install shutdown signal: {error}");
        }
    };
    #[cfg(feature = "portfolio")]
    if std::env::var("PORTFOLIO_ENABLED")
        .unwrap_or_else(|_| "true".into())
        .eq_ignore_ascii_case("true")
    {
        return terminal_server::portfolio::serve_with_portfolios(
            listener,
            config,
            terminal_server::portfolio::storage_path(),
            shutdown,
        )
        .await;
    }
    terminal_server::serve(listener, config, shutdown).await?;
    Ok(())
}
