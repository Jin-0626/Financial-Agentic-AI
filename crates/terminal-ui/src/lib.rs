//! Typed presentation boundary shared by the Tauri host and Rust engine.
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use terminal_core::{Action, HistoryRange, InstrumentId, Snapshot, TerminalError};
use terminal_engine::EngineHandle;

/// Transport delta: unchanged candle buffers stay native on quote-only updates.
#[derive(Clone, Serialize)]
pub struct DesktopUpdate<'a> {
    pub revision: u64,
    pub watchlist: &'a terminal_core::Watchlist,
    pub watchlists: &'a [terminal_core::watchlists::NamedWatchlist],
    pub preferences: &'a terminal_core::Preferences,
    pub quotes: &'a std::collections::BTreeMap<
        InstrumentId,
        Arc<terminal_core::Market<terminal_core::Quote>>,
    >,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub candles: Option<&'a terminal_core::Market<Arc<[terminal_core::Candle]>>>,
    pub mode: &'a str,
    pub message: &'a str,
    pub latency_ms: Option<u128>,
    pub quitting: bool,
}
impl<'a> DesktopUpdate<'a> {
    pub fn new(snapshot: &'a Snapshot, include_candles: bool) -> Self {
        Self {
            revision: snapshot.revision,
            watchlist: &snapshot.watchlist,
            watchlists: &snapshot.watchlists,
            preferences: &snapshot.preferences,
            quotes: &snapshot.quotes,
            candles: include_candles.then_some(snapshot.candles.as_ref()),
            mode: snapshot.mode,
            message: &snapshot.message,
            latency_ms: snapshot.latency_ms,
            quitting: snapshot.quitting,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum DesktopAction {
    View {
        view: terminal_core::workspace::DesktopViewSettings,
    },
    Select {
        symbol: InstrumentId,
    },
    SetRange {
        range: HistoryRange,
    },
    Command {
        text: String,
    },
    Save,
    Keymap {
        keymap: Vec<terminal_core::KeyBinding>,
    },
    Workspace {
        workspace: Box<terminal_core::workspace::Workspace>,
    },
    Quit,
}
pub fn snapshot(engine: &EngineHandle) -> Arc<Snapshot> {
    engine.snapshot()
}
pub fn dispatch(engine: &EngineHandle, action: DesktopAction) -> Result<(), TerminalError> {
    let action = match action {
        DesktopAction::View { view } => {
            view.validate()?;
            Action::ConfigureView(view)
        }
        DesktopAction::Select { symbol } => Action::SelectSymbol(symbol),
        DesktopAction::SetRange { range } => Action::SetRange(range),
        DesktopAction::Command { text } => {
            if text.len() > 1024 {
                return Err(TerminalError::InvalidInput(
                    "Command exceeds 1024 bytes".into(),
                ));
            }
            Action::ExecuteCommand(text)
        }
        DesktopAction::Save => Action::ExecuteCommand(":w".into()),
        DesktopAction::Keymap { keymap } => {
            terminal_core::validate_keymap(&keymap)?;
            Action::ConfigureKeys(keymap)
        }
        DesktopAction::Workspace { workspace } => {
            workspace.validate()?;
            Action::SaveWorkspace(*workspace)
        }
        DesktopAction::Quit => {
            engine.stop();
            return Ok(());
        }
    };
    engine.dispatch(action)
}
