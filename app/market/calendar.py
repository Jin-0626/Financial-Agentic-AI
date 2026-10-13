import logging
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from app.market import common
from app.market import macro
from app.market import transport

logger = logging.getLogger(__name__)

def calendar_series(row):
    title = str(row.get("title", "")).lower()
    country = row.get("country")
    if country == "USD" and "cpi" in title:
        return (
            "CPILFESL" if "core" in title else "CPIAUCSL",
            "inflation",
            "US Bureau of Labor Statistics",
        )
    if country == "USD" and ("non-farm" in title or "nonfarm" in title):
        return ("PAYEMS", "change", "US Bureau of Labor Statistics")
    if country == "USD" and "federal funds" in title:
        return ("DFEDTARU", "daily", "Board of Governors of the Federal Reserve System")
    if country == "EUR" and "refinancing" in title:
        return ("ECBMRRFR", "daily", "European Central Bank")
    if country == "EUR" and "deposit facility" in title:
        return ("ECBDFR", "daily", "European Central Bank")
    return None


def verified_calendar_actual(row, observations):
    """Only derive an outcome when the feed identifies its reference period."""
    period = row.get("reference_period")
    spec = calendar_series(row)
    if not spec or not isinstance(period, str):
        return None
    series_id, method, publisher = spec
    pattern = r"\d{4}-\d{2}-\d{2}" if method == "daily" else r"\d{4}-\d{2}"
    if not re.fullmatch(pattern, period):
        return None
    title = str(row.get("title", "")).lower()
    target = next(
        (
            obs
            for obs in observations
            if (
                obs.get("date") == period
                if method == "daily"
                else str(obs.get("date", "")).startswith(period + "-")
            )
            and obs.get("value") != "."
        ),
        None,
    )
    if target is None:
        return None
    try:
        year, month = map(int, period[:7].split("-"))
        datetime(year, month, 1)
        if method == "daily":
            datetime.fromisoformat(period)
            value = Decimal(target["value"])
            units = "percent"
        else:
            if method == "inflation" and "y/y" in title:
                previous = f"{year - 1:04d}-{month:02d}"
            elif method == "change" or "m/m" in title:
                previous = (
                    f"{year - 1:04d}-12"
                    if month == 1
                    else f"{year:04d}-{month - 1:02d}"
                )
            else:
                return None
            base = next(
                (
                    obs
                    for obs in observations
                    if str(obs.get("date", "")).startswith(previous + "-")
                    and obs.get("value") != "."
                ),
                None,
            )
            if base is None:
                return None
            value = (
                Decimal(target["value"]) - Decimal(base["value"])
                if method == "change"
                else (Decimal(target["value"]) / Decimal(base["value"]) - 1) * 100
            )
            units = "thousand persons" if method == "change" else "percent"
        if not value.is_finite():
            return None
        formatted = format(value.quantize(Decimal("0.1")), "f") + (
            "%" if units == "percent" else " thousand persons"
        )
        return {
            "value": formatted,
            "units": units,
            "reference_period": period,
            "source": "FRED / " + publisher,
            "series_id": series_id,
            "url": "https://fred.stlouisfed.org/series/" + series_id,
        }
    except (ValueError, InvalidOperation, ZeroDivisionError, TypeError, KeyError):
        return None


def economic_calendar():
    rows = transport.fetch("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    if not isinstance(rows, list):
        raise TypeError("Invalid economic calendar response")
    events = []
    observations = {}
    for row in rows[:500]:
        outcome = None
        when = row.get("date", "")
        try:
            released = datetime.fromisoformat(when) <= datetime.now(UTC)
        except (ValueError, TypeError):
            released = False
        spec = calendar_series(row)
        if (
            released
            and row.get("reference_period")
            and spec
            and os.getenv("FRED_API_KEY")
        ):
            series_id = spec[0]
            vintage = when[:10]
            key = (series_id, vintage)
            if key not in observations:
                try:
                    observations[key] = macro.fred_series(series_id, vintage)
                except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
                    observations[key] = []
            outcome = verified_calendar_actual(row, observations[key])
            if outcome:
                outcome["vintage_date"] = vintage
        events.append(
            {
                "title": row.get("title"),
                "country": row.get("country"),
                "date": row.get("date"),
                "impact": row.get("impact"),
                "forecast": row.get("forecast") or None,
                "previous": row.get("previous") or None,
                "actual": outcome["value"] if outcome else None,
                "actual_evidence": outcome,
                "actual_status": "Reference-period-matched official observation"
                if outcome
                else "No event-matched release observation verified",
            }
        )
    return common.result(
        events,
        "Forex Factory public calendar / FRED where matched",
        "Current-week public schedule; actual outcomes require event-matched official evidence",
        limitations=[
            "Malaysian release coverage may be absent; many events lack the reference period needed for safe actual matching"
        ],
    )


@transport.yahoo_bound
def earnings(key):
    asset = common.instrument(key)
    if asset["asset_class"] == "crypto":
        return {
            **common.result([], "Yahoo Finance", "Earnings do not apply to crypto"),
            "status": "unavailable",
        }
    ticker = transport.yf.Ticker(asset["symbol"])
    frame = ticker.get_earnings_dates(limit=12)
    if frame is not None:
        frame = frame.sort_index(ascending=False).head(12)
    rows = []
    history = None
    if frame is not None and not frame.empty:
        verified = [
            when
            for when in frame.index
            if getattr(when, "tzinfo", None) is not None
            and (when.hour or when.minute)
            and when.to_pydatetime() < datetime.now(UTC)
        ]
        if verified:
            try:
                history = ticker.history(
                    start=(min(verified) - timedelta(days=7)).date().isoformat(),
                    end=datetime.now(UTC).date().isoformat(),
                    auto_adjust=True,
                    actions=False,
                    timeout=10,
                )
            except Exception:  # noqa: BLE001 -- isolate third-party provider failures; expose partial coverage
                history = None
        for when, row in frame.iterrows():
            move = None
            reason = "Announcement timing and next trading session not verified"
            if when in verified and history is not None and not history.empty:
                local = (
                    when.tz_convert(history.index.tz)
                    if history.index.tz is not None
                    else None
                )
                if local is not None:
                    bars = sorted(
                        (stamp.date(), common.number(price))
                        for stamp, price in history["Close"].items()
                        if common.number(price) is not None and price > 0
                    )
                    next_index = next(
                        (i for i, (day, _) in enumerate(bars) if day > local.date()),
                        None,
                    )
                    if next_index is not None and next_index > 0:
                        move = (bars[next_index][1] / bars[next_index - 1][1] - 1) * 100
                        reason = "Adjusted close-to-close return for first trading date after reported announcement date; not a causal attribution"
            rows.append(
                {
                    "date": when.isoformat(),
                    "eps_estimate": common.number(row.get("EPS Estimate")),
                    "eps_actual": common.number(row.get("Reported EPS")),
                    "surprise_percent": common.number(row.get("Surprise(%)")),
                    "next_session_move_percent": move,
                    "move_status": reason,
                }
            )
    return common.result(
        rows,
        "Yahoo Finance",
        "Provider earnings records; Bursa coverage and release timing may be incomplete",
        instrument=asset,
    )

