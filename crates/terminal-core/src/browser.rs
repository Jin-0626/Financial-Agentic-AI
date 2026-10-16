//! Identity-scoped market and options presentation settings.
use crate::{resources::ScreenerSort, TerminalError};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Universe {
    MY,
    US,
    #[serde(rename = "crypto")]
    Crypto,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketColumn {
    Instrument,
    Name,
    Price,
    ChangePercent,
    Volume,
    MarketCap,
    Sector,
    Currency,
    Exchange,
    Status,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct MarketSettings {
    pub columns: Vec<MarketColumn>,
    pub sort: ScreenerSort,
    pub descending: bool,
    pub sector: Option<String>,
    pub min_cap: Option<Decimal>,
    pub min_change: Option<Decimal>,
    pub min_volume: Option<Decimal>,
    pub filter: String,
}
impl Default for MarketSettings {
    fn default() -> Self {
        Self {
            columns: vec![
                MarketColumn::Instrument,
                MarketColumn::Price,
                MarketColumn::ChangePercent,
                MarketColumn::MarketCap,
                MarketColumn::Volume,
                MarketColumn::Sector,
            ],
            sort: ScreenerSort::MarketCap,
            descending: true,
            sector: None,
            min_cap: None,
            min_change: None,
            min_volume: None,
            filter: String::new(),
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OptionColumn {
    Bid,
    Ask,
    Last,
    ImpliedVolatility,
    Volume,
    OpenInterest,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OptionSide {
    Calls,
    #[default]
    Both,
    Puts,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Moneyness {
    #[default]
    All,
    Itm,
    Otm,
}
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StrikeWindow {
    #[default]
    All,
    Nearest21,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct OptionsSettings {
    pub columns: Vec<OptionColumn>,
    pub side: OptionSide,
    pub moneyness: Moneyness,
    pub min_volume: Option<Decimal>,
    pub min_open_interest: Option<Decimal>,
    pub search: String,
    pub strike_window: StrikeWindow,
}
impl Default for OptionsSettings {
    fn default() -> Self {
        Self {
            columns: vec![
                OptionColumn::Bid,
                OptionColumn::Ask,
                OptionColumn::Last,
                OptionColumn::ImpliedVolatility,
                OptionColumn::OpenInterest,
                OptionColumn::Volume,
            ],
            side: OptionSide::Both,
            moneyness: Moneyness::All,
            min_volume: None,
            min_open_interest: None,
            search: String::new(),
            strike_window: StrikeWindow::All,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Preset<T> {
    pub name: String,
    pub settings: T,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct BrowserPreferences {
    pub my: MarketSettings,
    pub us: MarketSettings,
    pub crypto: MarketSettings,
    pub options: OptionsSettings,
    pub market_presets: Vec<Preset<MarketSettings>>,
    pub options_presets: Vec<Preset<OptionsSettings>>,
}
pub fn validate_filters(
    cap: Option<Decimal>,
    change: Option<Decimal>,
    volume: Option<Decimal>,
) -> bool {
    cap.is_none_or(|v| v >= Decimal::ZERO && v.to_string().parse::<f64>().is_ok_and(f64::is_finite))
        && change.is_none_or(|v| v.to_string().parse::<f64>().is_ok_and(f64::is_finite))
        && volume.is_none_or(|v| {
            v >= Decimal::ZERO && v.fract() == Decimal::ZERO && v <= Decimal::from(i64::MAX)
        })
}
fn invalid() -> TerminalError {
    TerminalError::InvalidInput("Invalid browser settings or preset".into())
}
fn columns<T: Ord>(values: &[T], maximum: usize) -> bool {
    !values.is_empty()
        && values.len() <= maximum
        && values.iter().collect::<BTreeSet<_>>().len() == values.len()
}
impl MarketSettings {
    pub fn validate(&self) -> Result<(), TerminalError> {
        if !columns(&self.columns, 10)
            || !self.columns.contains(&MarketColumn::Instrument)
            || self.filter.len() > 80
            || self.sector.as_ref().is_some_and(|s| s.len() > 80)
            || !validate_filters(self.min_cap, self.min_change, self.min_volume)
        {
            return Err(invalid());
        }
        Ok(())
    }
}
impl OptionsSettings {
    pub fn validate(&self) -> Result<(), TerminalError> {
        if !columns(&self.columns, 6)
            || self.search.len() > 80
            || !validate_filters(None, None, self.min_volume)
            || !validate_filters(None, None, self.min_open_interest)
        {
            return Err(invalid());
        }
        Ok(())
    }
}
impl BrowserPreferences {
    pub fn market(&self, market: Universe) -> &MarketSettings {
        match market {
            Universe::MY => &self.my,
            Universe::US => &self.us,
            Universe::Crypto => &self.crypto,
        }
    }
    fn market_mut(&mut self, market: Universe) -> &mut MarketSettings {
        match market {
            Universe::MY => &mut self.my,
            Universe::US => &mut self.us,
            Universe::Crypto => &mut self.crypto,
        }
    }
    pub fn validate(&self) -> Result<(), TerminalError> {
        self.my.validate()?;
        self.us.validate()?;
        self.crypto.validate()?;
        self.options.validate()?;
        for presets in [
            &self
                .market_presets
                .iter()
                .map(|p| p.name.as_str())
                .collect::<Vec<_>>(),
            &self
                .options_presets
                .iter()
                .map(|p| p.name.as_str())
                .collect::<Vec<_>>(),
        ] {
            if presets.len() > 20
                || presets
                    .iter()
                    .any(|n| n.trim().is_empty() || n.chars().count() > 64 || n.trim() != *n)
                || presets.iter().collect::<BTreeSet<_>>().len() != presets.len()
            {
                return Err(invalid());
            }
        }
        for preset in &self.market_presets {
            preset.settings.validate()?;
        }
        for preset in &self.options_presets {
            preset.settings.validate()?;
        }
        Ok(())
    }
    pub fn apply(&mut self, mutation: BrowserMutation) -> Result<(), TerminalError> {
        match mutation {
            BrowserMutation::Market { market, settings } => {
                settings.validate()?;
                *self.market_mut(market) = *settings;
            }
            BrowserMutation::Options { settings } => {
                settings.validate()?;
                self.options = *settings;
            }
            BrowserMutation::MarketPreset { market, action } => {
                let mut current = self.market(market).clone();
                preset_action(&mut self.market_presets, &mut current, action)?;
                *self.market_mut(market) = current;
            }
            BrowserMutation::OptionsPreset { action } => {
                preset_action(&mut self.options_presets, &mut self.options, action)?
            }
        }
        self.validate()
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum PresetAction {
    Save {
        name: String,
        duplicate_from: Option<String>,
    },
    Rename {
        name: String,
        new_name: String,
    },
    Delete {
        name: String,
    },
    Apply {
        name: String,
    },
    Reset,
}
fn preset_action<T: Clone + Default>(
    presets: &mut Vec<Preset<T>>,
    current: &mut T,
    action: PresetAction,
) -> Result<(), TerminalError> {
    match action {
        PresetAction::Save {
            name,
            duplicate_from,
        } => {
            if presets.len() >= 20 || presets.iter().any(|p| p.name == name) {
                return Err(invalid());
            }
            let settings = match duplicate_from {
                Some(from) => presets
                    .iter()
                    .find(|p| p.name == from)
                    .ok_or_else(invalid)?
                    .settings
                    .clone(),
                None => current.clone(),
            };
            presets.push(Preset { name, settings });
        }
        PresetAction::Rename { name, new_name } => {
            if presets.iter().any(|p| p.name == new_name && p.name != name) {
                return Err(invalid());
            }
            presets
                .iter_mut()
                .find(|p| p.name == name)
                .ok_or_else(invalid)?
                .name = new_name;
        }
        PresetAction::Delete { name } => {
            let index = presets
                .iter()
                .position(|p| p.name == name)
                .ok_or_else(invalid)?;
            presets.remove(index);
        }
        PresetAction::Apply { name } => {
            *current = presets
                .iter()
                .find(|p| p.name == name)
                .ok_or_else(invalid)?
                .settings
                .clone()
        }
        PresetAction::Reset => *current = T::default(),
    }
    Ok(())
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum BrowserMutation {
    Market {
        market: Universe,
        settings: Box<MarketSettings>,
    },
    Options {
        settings: Box<OptionsSettings>,
    },
    MarketPreset {
        market: Universe,
        action: PresetAction,
    },
    OptionsPreset {
        action: PresetAction,
    },
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn presets_filters_columns_and_limits() -> Result<(), Box<dyn std::error::Error>> {
        let mut prefs = BrowserPreferences::default();
        let settings = MarketSettings {
            min_cap: Some("1000000.01".parse()?),
            min_change: Some("-2.5".parse()?),
            min_volume: Some("500".parse()?),
            ..MarketSettings::default()
        };
        prefs.apply(BrowserMutation::Market {
            market: Universe::MY,
            settings: Box::new(settings),
        })?;
        prefs.apply(BrowserMutation::MarketPreset {
            market: Universe::MY,
            action: PresetAction::Save {
                name: "My filters".into(),
                duplicate_from: None,
            },
        })?;
        prefs.apply(BrowserMutation::MarketPreset {
            market: Universe::US,
            action: PresetAction::Apply {
                name: "My filters".into(),
            },
        })?;
        assert_eq!(prefs.us.min_cap, prefs.my.min_cap);
        assert!(prefs.crypto.min_cap.is_none());
        prefs.apply(BrowserMutation::MarketPreset {
            market: Universe::US,
            action: PresetAction::Save {
                name: "Copy".into(),
                duplicate_from: Some("My filters".into()),
            },
        })?;
        prefs.apply(BrowserMutation::MarketPreset {
            market: Universe::US,
            action: PresetAction::Rename {
                name: "Copy".into(),
                new_name: "Renamed".into(),
            },
        })?;
        prefs.apply(BrowserMutation::MarketPreset {
            market: Universe::US,
            action: PresetAction::Delete {
                name: "Renamed".into(),
            },
        })?;
        assert_eq!(prefs.market_presets.len(), 1);
        assert!(prefs.options_presets.is_empty());
        let mut bad = MarketSettings {
            columns: vec![MarketColumn::Price],
            ..MarketSettings::default()
        };
        assert!(bad.validate().is_err());
        bad = MarketSettings::default();
        bad.min_volume = Some("0.1".parse()?);
        assert!(bad.validate().is_err());
        for i in 1..20 {
            prefs.apply(BrowserMutation::MarketPreset {
                market: Universe::MY,
                action: PresetAction::Save {
                    name: format!("Preset {i}"),
                    duplicate_from: None,
                },
            })?;
        }
        assert!(prefs
            .apply(BrowserMutation::MarketPreset {
                market: Universe::MY,
                action: PresetAction::Save {
                    name: "Overflow".into(),
                    duplicate_from: None
                }
            })
            .is_err());
        let mut malformed = prefs.clone();
        malformed.market_presets[0].name = " ".into();
        assert!(malformed.validate().is_err());
        let request = crate::resources::ResourceRequest::Screener {
            market: crate::resources::EquityMarket::MY,
            sector: None,
            sort: ScreenerSort::Volume,
            descending: true,
            offset: 0,
            min_cap: None,
            min_change: None,
            min_volume: Some("1".parse()?),
        };
        request.validate()?;
        let mut other = serde_json::to_value(&request)?;
        other["min_volume"] = "2".into();
        let other: crate::resources::ResourceRequest = serde_json::from_value(other)?;
        assert_ne!(request, other);
        Ok(())
    }
}
