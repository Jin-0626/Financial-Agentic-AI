//! Local chart analysis settings and completed drawing mutations.
use crate::{InstrumentId, TerminalError};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChartStyle {
    #[default]
    Candles,
    Line,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Indicator {
    pub enabled: bool,
    pub periods: Vec<u16>,
    pub color: String,
    pub width: u8,
}
fn indicator(periods: &[u16], color: &str, enabled: bool) -> Indicator {
    Indicator {
        enabled,
        periods: periods.into(),
        color: color.into(),
        width: 1,
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ChartSettings {
    pub style: ChartStyle,
    pub sma: Indicator,
    pub ema: Indicator,
    pub bollinger: Indicator,
    pub rsi: Indicator,
    pub macd: Indicator,
    pub volume: Indicator,
}
impl Default for ChartSettings {
    fn default() -> Self {
        Self {
            style: ChartStyle::Candles,
            sma: indicator(&[5, 10, 20], "#EA670C", false),
            ema: indicator(&[20], "#4E7FCE", false),
            bollinger: indicator(&[20], "#A78BFA", false),
            rsi: indicator(&[14], "#E8B44C", false),
            macd: indicator(&[12, 26, 9], "#4E7FCE", false),
            volume: indicator(&[], "#00A357", true),
        }
    }
}
fn color_valid(value: &str) -> bool {
    value.len() == 7 && value.starts_with('#') && value[1..].bytes().all(|b| b.is_ascii_hexdigit())
}
impl ChartSettings {
    pub fn validate(&self) -> Result<(), TerminalError> {
        for (value, count) in [
            (&self.sma, 3),
            (&self.ema, 1),
            (&self.bollinger, 1),
            (&self.rsi, 1),
            (&self.macd, 3),
            (&self.volume, 0),
        ] {
            if value.periods.len() != count
                || value.periods.iter().any(|p| !(2..=250).contains(p))
                || !(1..=4).contains(&value.width)
                || !color_valid(&value.color)
            {
                return Err(TerminalError::InvalidInput(
                    "Invalid indicator settings".into(),
                ));
            }
        }
        if self.macd.periods[0] >= self.macd.periods[1] {
            return Err(TerminalError::InvalidInput(
                "MACD fast period must be less than slow".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DrawingKind {
    Trend,
    Horizontal,
    Rectangle,
    Fibonacci,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Anchor {
    pub time: i64,
    pub price: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Drawing {
    pub id: String,
    pub kind: DrawingKind,
    pub anchors: [Anchor; 2],
    pub color: String,
    pub width: u8,
}
impl Drawing {
    pub fn validate(&self) -> Result<(), TerminalError> {
        if self.id.is_empty()
            || self.id.len() > 64
            || !color_valid(&self.color)
            || !(1..=4).contains(&self.width)
            || self
                .anchors
                .iter()
                .any(|a| !a.price.is_finite() || !(0..=253_402_300_799).contains(&a.time))
        {
            return Err(TerminalError::InvalidInput("Invalid chart drawing".into()));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ChartMutation {
    Settings {
        layout: String,
        panel: String,
        settings: Box<ChartSettings>,
    },
    Drawings {
        layout: String,
        panel: String,
        instrument: InstrumentId,
        drawings: Vec<Drawing>,
    },
}
impl ChartMutation {
    pub fn apply(self, workspace: &mut crate::workspace::Workspace) -> Result<(), TerminalError> {
        let (layout, panel) = match &self {
            Self::Settings { layout, panel, .. } | Self::Drawings { layout, panel, .. } => {
                (layout, panel)
            }
        };
        let target = workspace
            .layouts
            .iter_mut()
            .find(|l| &l.name == layout)
            .and_then(|l| l.panels.get_mut(panel))
            .filter(|p| p.kind == crate::workspace::PanelKind::Chart)
            .ok_or_else(|| TerminalError::InvalidInput("Chart panel no longer exists".into()))?;
        match self {
            Self::Settings { settings, .. } => {
                settings.validate()?;
                target.chart = *settings;
            }
            Self::Drawings {
                instrument,
                drawings,
                ..
            } => {
                validate_drawings(&drawings)?;
                if drawings.is_empty() {
                    target.drawings.remove(&instrument);
                } else {
                    target.drawings.insert(instrument, drawings);
                }
            }
        }
        workspace.validate()
    }
}
pub fn validate_drawings(drawings: &[Drawing]) -> Result<(), TerminalError> {
    let mut ids = std::collections::BTreeSet::new();
    if drawings.len() > 100 {
        return Err(TerminalError::InvalidInput(
            "Maximum 100 drawings per chart/instrument".into(),
        ));
    }
    for d in drawings {
        d.validate()?;
        if !ids.insert(&d.id) {
            return Err(TerminalError::InvalidInput("Duplicate drawing ID".into()));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn settings_and_drawings_are_bounded() -> Result<(), Box<dyn std::error::Error>> {
        let mut settings = ChartSettings::default();
        settings.validate()?;
        settings.macd.periods = vec![26, 12, 9];
        assert!(settings.validate().is_err());
        settings = ChartSettings::default();
        settings.sma.periods[0] = 251;
        assert!(settings.validate().is_err());
        let mut drawing = Drawing {
            id: "a".into(),
            kind: DrawingKind::Trend,
            anchors: [Anchor {
                time: 100,
                price: 1.2,
            }; 2],
            color: "#EA670C".into(),
            width: 1,
        };
        drawing.validate()?;
        assert!(validate_drawings(&[drawing.clone(), drawing.clone()]).is_err());
        drawing.anchors[0].price = f64::NAN;
        assert!(drawing.validate().is_err());
        Ok(())
    }
    #[test]
    fn panel_and_instrument_isolation() -> Result<(), Box<dyn std::error::Error>> {
        let mut workspace = crate::workspace::Workspace::default();
        let layout = workspace.active_layout.clone();
        let instrument = InstrumentId::parse("5326.KL")?;
        let drawing = Drawing {
            id: "a".into(),
            kind: DrawingKind::Horizontal,
            anchors: [Anchor {
                time: 100,
                price: 3.0,
            }; 2],
            color: "#EA670C".into(),
            width: 1,
        };
        ChartMutation::Drawings {
            layout: layout.clone(),
            panel: "chart".into(),
            instrument: instrument.clone(),
            drawings: vec![drawing],
        }
        .apply(&mut workspace)?;
        assert_eq!(
            workspace.layouts[0].panels["chart"].drawings[&instrument].len(),
            1
        );
        assert!(workspace.layouts[0].panels["watchlist"].drawings.is_empty());
        assert!(ChartMutation::Settings {
            layout,
            panel: "closed".into(),
            settings: Box::default()
        }
        .apply(&mut workspace)
        .is_err());
        Ok(())
    }
}
