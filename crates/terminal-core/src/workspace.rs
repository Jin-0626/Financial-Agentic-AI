//! Persisted desktop layout. Bounds keep untrusted IPC and saved trees small.
use crate::TerminalError;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PanelKind {
    Chart,
    Watchlist,
    News,
    Macro,
    Calendar,
    Screener,
    Research,
    Portfolio,
}
impl PanelKind {
    pub fn legacy(value: &str) -> Option<Self> {
        match value {
            "chart" => Some(Self::Chart),
            "watchlist" => Some(Self::Watchlist),
            "news" => Some(Self::News),
            "macro" => Some(Self::Macro),
            "calendar" => Some(Self::Calendar),
            "screener" => Some(Self::Screener),
            "research" => Some(Self::Research),
            "portfolio" => Some(Self::Portfolio),
            _ => None,
        }
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum PanelBinding {
    #[default]
    Linked,
    Pinned {
        instrument: crate::InstrumentId,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PanelSpec {
    pub kind: PanelKind,
    #[serde(default)]
    pub binding: PanelBinding,
    #[serde(default)]
    pub range: Option<crate::HistoryRange>,
    #[serde(default)]
    pub watchlist_id: Option<crate::watchlists::WatchlistId>,
    #[serde(default)]
    pub table: crate::watchlists::TableSettings,
    #[serde(default)]
    pub chart: crate::chart::ChartSettings,
    #[serde(default)]
    pub drawings: BTreeMap<crate::InstrumentId, Vec<crate::chart::Drawing>>,
}
#[derive(Debug, Clone, Serialize)]
pub struct ConnectionInfo {
    pub mode: String,
    pub backend_url: Option<String>,
    pub organization: Option<String>,
    pub user: Option<String>,
    pub profile: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Screen {
    Dashboard,
    Markets,
    Watchlist,
    Crypto,
    Portfolio,
    Research,
    News,
    Screener,
    Options,
    Macro,
    Calendar,
    Backtesting,
    Traces,
    Settings,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Axis {
    Horizontal,
    Vertical,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum DockNode {
    Tabs {
        id: String,
        panels: Vec<String>,
        active: String,
    },
    Split {
        id: String,
        axis: Axis,
        ratio: f64,
        first: Box<Self>,
        second: Box<Self>,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NamedLayout {
    pub name: String,
    pub root: DockNode,
    #[serde(default)]
    pub panels: BTreeMap<String, PanelSpec>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Workspace {
    pub screen: Screen,
    pub compact: bool,
    pub active_layout: String,
    pub layouts: Vec<NamedLayout>,
    #[serde(default)]
    pub desktop: DesktopViewSettings,
    #[serde(default)]
    pub browser: crate::browser::BrowserPreferences,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InspectorTab {
    #[default]
    Quote,
    News,
    Calendar,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TickerMode {
    #[default]
    Indices,
    Watchlist,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketTab {
    #[default]
    Overview,
    #[serde(rename = "MY")]
    Malaysia,
    #[serde(rename = "US")]
    UnitedStates,
    Crypto,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PortfolioTab {
    #[default]
    Holdings,
    Transactions,
    Performance,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CalendarTab {
    #[default]
    Economic,
    Earnings,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CalendarMode {
    #[default]
    Week,
    Table,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct DesktopViewSettings {
    pub rail_collapsed: bool,
    pub inspector_open: bool,
    pub inspector_width: u16,
    pub inspector_tab: InspectorTab,
    pub ticker_mode: TickerMode,
    pub market_tab: MarketTab,
    pub portfolio_tab: PortfolioTab,
    pub calendar_tab: CalendarTab,
    pub calendar_mode: CalendarMode,
}
impl Default for DesktopViewSettings {
    fn default() -> Self {
        Self {
            rail_collapsed: false,
            inspector_open: false,
            inspector_width: 290,
            inspector_tab: InspectorTab::default(),
            ticker_mode: TickerMode::default(),
            market_tab: MarketTab::default(),
            portfolio_tab: PortfolioTab::default(),
            calendar_tab: CalendarTab::default(),
            calendar_mode: CalendarMode::default(),
        }
    }
}
impl DesktopViewSettings {
    pub fn validate(&self) -> Result<(), TerminalError> {
        if !(260..=420).contains(&self.inspector_width) {
            return Err(TerminalError::InvalidInput(
                "Inspector width must be 260–420 pixels".into(),
            ));
        }
        Ok(())
    }
}
impl Default for Workspace {
    fn default() -> Self {
        Self {
            screen: Screen::Dashboard,
            compact: true,
            desktop: DesktopViewSettings::default(),
            browser: crate::browser::BrowserPreferences::default(),
            active_layout: "Market Desk".into(),
            layouts: vec![NamedLayout {
                name: "Market Desk".into(),
                panels: BTreeMap::from([
                    (
                        "chart".into(),
                        PanelSpec {
                            kind: PanelKind::Chart,
                            binding: PanelBinding::Linked,
                            range: None,
                            watchlist_id: None,
                            table: crate::watchlists::TableSettings::default(),
                            chart: crate::chart::ChartSettings::default(),
                            drawings: BTreeMap::new(),
                        },
                    ),
                    (
                        "watchlist".into(),
                        PanelSpec {
                            kind: PanelKind::Watchlist,
                            binding: PanelBinding::Linked,
                            range: None,
                            watchlist_id: Some(crate::watchlists::WatchlistId::default()),
                            table: crate::watchlists::TableSettings::default(),
                            chart: crate::chart::ChartSettings::default(),
                            drawings: BTreeMap::new(),
                        },
                    ),
                ]),
                root: DockNode::Split {
                    id: "root".into(),
                    axis: Axis::Horizontal,
                    ratio: 0.19,
                    first: Box::new(DockNode::Tabs {
                        id: "watchlist-group".into(),
                        panels: vec!["watchlist".into()],
                        active: "watchlist".into(),
                    }),
                    second: Box::new(DockNode::Tabs {
                        id: "chart-group".into(),
                        panels: vec!["chart".into()],
                        active: "chart".into(),
                    }),
                },
            }],
        }
    }
}
pub fn legacy_default() -> Workspace {
    let mut value = Workspace {
        active_layout: "Market Overview".into(),
        compact: false,
        ..Workspace::default()
    };
    value.layouts[0].name = "Market Overview".into();
    if let DockNode::Split {
        ratio,
        first,
        second,
        ..
    } = &mut value.layouts[0].root
    {
        *ratio = 0.7;
        std::mem::swap(first, second);
    }
    value
}
impl Workspace {
    pub fn migrate(&mut self) -> Result<(), TerminalError> {
        for layout in &mut self.layouts {
            let mut ids = BTreeSet::new();
            fn collect(node: &DockNode, ids: &mut BTreeSet<String>) {
                match node {
                    DockNode::Tabs { panels, .. } => ids.extend(panels.iter().cloned()),
                    DockNode::Split { first, second, .. } => {
                        collect(first, ids);
                        collect(second, ids);
                    }
                }
            }
            collect(&layout.root, &mut ids);
            for id in ids {
                if let std::collections::btree_map::Entry::Vacant(entry) = layout.panels.entry(id) {
                    let kind = PanelKind::legacy(entry.key()).ok_or_else(|| {
                        TerminalError::InvalidInput("Unknown legacy panel".into())
                    })?;
                    entry.insert(PanelSpec {
                        kind,
                        binding: PanelBinding::Linked,
                        range: None,
                        watchlist_id: Some(crate::watchlists::WatchlistId::default()),
                        table: crate::watchlists::TableSettings::default(),
                        chart: crate::chart::ChartSettings::default(),
                        drawings: BTreeMap::new(),
                    });
                }
            }
        }
        self.validate()
    }
    /// Rename the former built-in preset without overwriting a user's layout.
    pub fn rename_legacy_market_desk(&mut self) -> bool {
        let mut changed = false;
        for index in 0..self.layouts.len() {
            if !self.layouts[index]
                .name
                .eq_ignore_ascii_case("Moomoo Market Desk")
            {
                continue;
            }
            let mut name = "Market Desk".to_owned();
            let mut suffix = 2;
            while self.layouts.iter().any(|layout| layout.name == name) {
                name = format!("Market Desk ({suffix})");
                suffix += 1;
            }
            if self.active_layout == self.layouts[index].name {
                self.active_layout = name.clone();
            }
            self.layouts[index].name = name;
            changed = true;
        }
        changed
    }
    pub fn validate(&self) -> Result<(), TerminalError> {
        self.desktop.validate()?;
        self.browser.validate()?;
        let invalid = || TerminalError::InvalidInput("Invalid desktop workspace".into());
        if self.layouts.is_empty() || self.layouts.len() > 12 {
            return Err(invalid());
        }
        let mut names = BTreeSet::new();
        for layout in &self.layouts {
            for panel in layout.panels.values() {
                panel.table.validate()?;
                panel.chart.validate()?;
                if panel.drawings.len() > 128 {
                    return Err(invalid());
                }
                for drawings in panel.drawings.values() {
                    crate::chart::validate_drawings(drawings)?;
                }
            }
            if layout.name.trim().is_empty()
                || layout.name.len() > 64
                || !names.insert(&layout.name)
            {
                return Err(invalid());
            }
            let mut nodes = BTreeSet::new();
            let mut panels = BTreeSet::new();
            validate_node(&layout.root, 0, &mut nodes, &mut panels)?;
            if layout.panels.is_empty()
                || layout.panels.len() > 32
                || panels.iter().any(|id| !layout.panels.contains_key(*id))
            {
                return Err(invalid());
            }
        }
        if !names.contains(&self.active_layout) {
            return Err(invalid());
        }
        Ok(())
    }
}
fn validate_node<'a>(
    node: &'a DockNode,
    depth: usize,
    nodes: &mut BTreeSet<&'a str>,
    panels: &mut BTreeSet<&'a str>,
) -> Result<(), TerminalError> {
    let invalid = || TerminalError::InvalidInput("Invalid dock tree".into());
    let id = match node {
        DockNode::Tabs { id, .. } | DockNode::Split { id, .. } => id,
    };
    if depth > 8 || id.is_empty() || id.len() > 64 || !nodes.insert(id) || nodes.len() > 63 {
        return Err(invalid());
    }
    match node {
        DockNode::Tabs {
            panels: tabs,
            active,
            ..
        } => {
            if tabs.is_empty() || tabs.len() > 16 || !tabs.contains(active) {
                return Err(invalid());
            }
            for panel in tabs {
                if panel.is_empty()
                    || panel.len() > 64
                    || !panel
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
                    || !panels.insert(panel)
                {
                    return Err(invalid());
                }
            }
        }
        DockNode::Split {
            ratio,
            first,
            second,
            ..
        } => {
            if !ratio.is_finite() || !(0.15..=0.85).contains(ratio) {
                return Err(invalid());
            }
            validate_node(first, depth + 1, nodes, panels)?;
            validate_node(second, depth + 1, nodes, panels)?;
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_roundtrip_and_duplicate_rejection() -> Result<(), Box<dyn std::error::Error>> {
        let mut workspace: Workspace =
            serde_json::from_str(&serde_json::to_string(&Workspace::default())?)?;
        workspace.validate()?;
        workspace.layouts.push(workspace.layouts[0].clone());
        assert!(workspace.validate().is_err());
        Ok(())
    }
    #[test]
    fn legacy_preset_renaming_preserves_layouts_and_active_selection() {
        let mut workspace = Workspace::default();
        let mut legacy = workspace.layouts[0].clone();
        legacy.name = "Moomoo Market Desk".into();
        workspace.active_layout = legacy.name.clone();
        workspace.layouts.push(legacy);
        assert!(workspace.rename_legacy_market_desk());
        assert_eq!(workspace.layouts.len(), 2);
        assert_eq!(workspace.layouts[0].name, "Market Desk");
        assert_eq!(workspace.active_layout, "Market Desk (2)");
        assert_eq!(workspace.layouts[1].name, "Market Desk (2)");
        assert!(workspace.validate().is_ok());
        assert!(!workspace.rename_legacy_market_desk());
    }
    #[test]
    fn invalid_ratio_rejected() {
        let mut workspace = Workspace::default();
        if let DockNode::Split { ratio, .. } = &mut workspace.layouts[0].root {
            *ratio = f64::NAN;
        }
        assert!(workspace.validate().is_err());
    }
}
