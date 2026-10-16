//! Local, identity-scoped named watchlists and atomic mutations.
use crate::{InstrumentId, TerminalError, MAX_WATCHLIST};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

pub const MAX_LISTS: usize = 20;
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(transparent)]
pub struct WatchlistId(String);
impl WatchlistId {
    pub fn parse(value: &str) -> Result<Self, TerminalError> {
        if value.is_empty()
            || value.len() > 64
            || !value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err(TerminalError::InvalidInput("invalid watchlist ID".into()));
        }
        Ok(Self(value.into()))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl Default for WatchlistId {
    fn default() -> Self {
        Self("main".into())
    }
}
impl<'de> Deserialize<'de> for WatchlistId {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Self::parse(&String::deserialize(d)?).map_err(serde::de::Error::custom)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NamedWatchlist {
    pub id: WatchlistId,
    pub name: String,
    pub symbols: Vec<InstrumentId>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum WatchlistMutation {
    Create {
        id: WatchlistId,
        name: String,
        duplicate_from: Option<WatchlistId>,
    },
    Rename {
        id: WatchlistId,
        name: String,
    },
    Delete {
        id: WatchlistId,
    },
    Order {
        ids: Vec<WatchlistId>,
    },
    Activate {
        id: WatchlistId,
    },
    Add {
        id: WatchlistId,
        symbols: Vec<InstrumentId>,
    },
    Remove {
        id: WatchlistId,
        symbol: InstrumentId,
    },
    Reorder {
        id: WatchlistId,
        symbols: Vec<InstrumentId>,
    },
}
pub fn validate(lists: &[NamedWatchlist], active: &WatchlistId) -> Result<(), TerminalError> {
    let invalid = || {
        TerminalError::InvalidInput(
            "watchlists require unique names/IDs, 1–20 lists and at most 128 distinct instruments"
                .into(),
        )
    };
    if lists.is_empty() || lists.len() > MAX_LISTS || !lists.iter().any(|l| &l.id == active) {
        return Err(invalid());
    }
    let mut ids = BTreeSet::new();
    let mut names = BTreeSet::new();
    let mut instruments = BTreeSet::new();
    for list in lists {
        if !ids.insert(&list.id)
            || list.name.trim().is_empty()
            || list.name.len() > 64
            || list.name.chars().any(char::is_control)
            || !names.insert(list.name.trim().to_lowercase())
            || list.symbols.len() > MAX_WATCHLIST
            || list.symbols.iter().collect::<BTreeSet<_>>().len() != list.symbols.len()
        {
            return Err(invalid());
        }
        instruments.extend(list.symbols.iter());
    }
    if instruments.len() > MAX_WATCHLIST {
        return Err(invalid());
    }
    Ok(())
}
pub fn apply(
    lists: &[NamedWatchlist],
    active: &WatchlistId,
    mutation: WatchlistMutation,
) -> Result<(Vec<NamedWatchlist>, WatchlistId), TerminalError> {
    let mut lists = lists.to_vec();
    let mut active = active.clone();
    let missing = || TerminalError::InvalidInput("watchlist no longer exists".into());
    match mutation {
        WatchlistMutation::Create {
            id,
            name,
            duplicate_from,
        } => {
            let symbols = match duplicate_from {
                Some(source) => lists
                    .iter()
                    .find(|l| l.id == source)
                    .ok_or_else(missing)?
                    .symbols
                    .clone(),
                None => Vec::new(),
            };
            lists.push(NamedWatchlist {
                id: id.clone(),
                name: name.trim().into(),
                symbols,
            });
            active = id;
        }
        WatchlistMutation::Rename { id, name } => {
            lists
                .iter_mut()
                .find(|l| l.id == id)
                .ok_or_else(missing)?
                .name = name.trim().into()
        }
        WatchlistMutation::Delete { id } => {
            if !lists.iter().any(|l| l.id == id) {
                return Err(missing());
            }
            lists.retain(|l| l.id != id);
            if active == id {
                active = lists.first().ok_or_else(missing)?.id.clone();
            }
        }
        WatchlistMutation::Order { ids } => {
            if ids.len() != lists.len() || ids.iter().collect::<BTreeSet<_>>().len() != ids.len() {
                return Err(missing());
            }
            lists = ids
                .iter()
                .map(|id| {
                    lists
                        .iter()
                        .find(|l| &l.id == id)
                        .cloned()
                        .ok_or_else(missing)
                })
                .collect::<Result<_, _>>()?;
        }
        WatchlistMutation::Activate { id } => active = id,
        WatchlistMutation::Add { id, symbols } => {
            let list = lists.iter_mut().find(|l| l.id == id).ok_or_else(missing)?;
            for symbol in symbols {
                if !list.symbols.contains(&symbol) {
                    list.symbols.push(symbol);
                }
            }
        }
        WatchlistMutation::Remove { id, symbol } => lists
            .iter_mut()
            .find(|l| l.id == id)
            .ok_or_else(missing)?
            .symbols
            .retain(|s| s != &symbol),
        WatchlistMutation::Reorder { id, symbols } => {
            let list = lists.iter_mut().find(|l| l.id == id).ok_or_else(missing)?;
            if symbols.len() != list.symbols.len()
                || symbols.iter().collect::<BTreeSet<_>>()
                    != list.symbols.iter().collect::<BTreeSet<_>>()
            {
                return Err(TerminalError::InvalidInput(
                    "reordering must preserve membership".into(),
                ));
            }
            list.symbols = symbols;
        }
    }
    validate(&lists, &active)?;
    Ok((lists, active))
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum QuoteColumn {
    Instrument,
    Name,
    Price,
    Change,
    Volume,
    Currency,
    Exchange,
    Status,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TableSettings {
    pub columns: Vec<QuoteColumn>,
    pub sort: Option<QuoteColumn>,
    pub descending: bool,
}
impl Default for TableSettings {
    fn default() -> Self {
        Self {
            columns: vec![
                QuoteColumn::Instrument,
                QuoteColumn::Price,
                QuoteColumn::Change,
            ],
            sort: None,
            descending: false,
        }
    }
}
impl TableSettings {
    pub fn validate(&self) -> Result<(), TerminalError> {
        if self.columns.is_empty()
            || self.columns.len() > 8
            || !self.columns.contains(&QuoteColumn::Instrument)
            || self
                .columns
                .iter()
                .enumerate()
                .any(|(i, c)| self.columns[..i].contains(c))
        {
            return Err(TerminalError::InvalidInput("invalid quote columns".into()));
        }
        Ok(())
    }
}
#[derive(Debug, Serialize)]
pub struct BulkPreview {
    pub symbols: Vec<InstrumentId>,
    pub duplicates: Vec<String>,
    pub invalid: Vec<String>,
    pub error: Option<String>,
}
pub fn preview(
    lists: &[NamedWatchlist],
    active: &WatchlistId,
    id: &WatchlistId,
    text: &str,
) -> Result<BulkPreview, TerminalError> {
    if text.len() > 16384 {
        return Err(TerminalError::InvalidInput(
            "bulk entry exceeds 16 KiB".into(),
        ));
    }
    let list = lists
        .iter()
        .find(|l| &l.id == id)
        .ok_or_else(|| TerminalError::InvalidInput("missing watchlist".into()))?;
    let mut result = BulkPreview {
        symbols: Vec::new(),
        duplicates: Vec::new(),
        invalid: Vec::new(),
        error: None,
    };
    for token in text
        .split(['\n', ',', ';'])
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        match InstrumentId::parse(token) {
            Ok(symbol) => {
                if list.symbols.contains(&symbol) || result.symbols.contains(&symbol) {
                    result.duplicates.push(symbol.as_str().into());
                } else {
                    result.symbols.push(symbol);
                }
            }
            Err(_) => result.invalid.push(token.into()),
        }
    }
    if let Err(error) = apply(
        lists,
        active,
        WatchlistMutation::Add {
            id: id.clone(),
            symbols: result.symbols.clone(),
        },
    ) {
        result.error = Some(error.to_string());
    }
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn initial() -> Vec<NamedWatchlist> {
        vec![NamedWatchlist {
            id: WatchlistId::default(),
            name: "My Watchlist".into(),
            symbols: vec![],
        }]
    }
    #[test]
    fn independent_membership_order_and_atomic_validation() -> Result<(), TerminalError> {
        let main = WatchlistId::default();
        let other = WatchlistId::parse("other")?;
        let apple = InstrumentId::parse("AAPL")?;
        let (lists, active) = apply(
            &initial(),
            &main,
            WatchlistMutation::Add {
                id: main.clone(),
                symbols: vec![apple.clone()],
            },
        )?;
        let (lists, active) = apply(
            &lists,
            &active,
            WatchlistMutation::Create {
                id: other.clone(),
                name: "US".into(),
                duplicate_from: Some(main.clone()),
            },
        )?;
        assert_eq!(lists[1].symbols, vec![apple.clone()]);
        let (lists, active) = apply(
            &lists,
            &active,
            WatchlistMutation::Remove {
                id: other.clone(),
                symbol: apple.clone(),
            },
        )?;
        assert_eq!(lists[0].symbols, vec![apple]);
        assert!(lists[1].symbols.is_empty());
        assert!(apply(
            &lists,
            &active,
            WatchlistMutation::Rename {
                id: other.clone(),
                name: " my watchlist ".into()
            }
        )
        .is_err());
        let (lists, active) = apply(&lists, &active, WatchlistMutation::Delete { id: other })?;
        assert_eq!(active, main);
        assert!(apply(&lists, &active, WatchlistMutation::Delete { id: main }).is_err());
        Ok(())
    }
    #[test]
    fn bulk_validation_and_namespace_capacity() -> Result<(), TerminalError> {
        let main = WatchlistId::default();
        let mut lists = initial();
        let result = preview(
            &lists,
            &main,
            &main,
            "aapl; yahoo:AAPL\ncoingecko:bitcoin,bad/symbol",
        )?;
        assert_eq!(result.symbols.len(), 2);
        assert_eq!(result.duplicates, vec!["yahoo:AAPL"]);
        assert_eq!(result.invalid, vec!["bad/symbol"]);
        lists[0].symbols = (0..128)
            .map(|i| InstrumentId::parse(&format!("S{i}")))
            .collect::<Result<_, _>>()?;
        let other = WatchlistId::parse("other")?;
        let (lists, active) = apply(
            &lists,
            &main,
            WatchlistMutation::Create {
                id: other.clone(),
                name: "Other".into(),
                duplicate_from: None,
            },
        )?;
        assert!(preview(&lists, &active, &other, "NEW")?.error.is_some());
        assert!(preview(&lists, &active, &other, "S0")?.error.is_none());
        Ok(())
    }
}
