import logging
from datetime import UTC, datetime, timedelta
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)


@transport.yahoo_bound
def ohlcv(key, range_key):
    asset = common.instrument(key)
    if asset["asset_class"] == "crypto":
        days = {
            "1D": 1,
            "5D": 7,
            "1M": 30,
            "6M": 180,
            "YTD": 365,
            "1Y": 365,
            "5Y": 1825,
            "MAX": "max",
        }[range_key]
        rows = transport.cg(
            "coins/" + key.split(":", 1)[1] + "/ohlc",
            {"vs_currency": "usd", "days": days},
        )
        candles = [
            {
                "time": int(row[0] / 1000),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": None,
            }
            for row in rows
        ]
        if range_key == "5D":
            candles = [
                c
                for c in candles
                if c["time"] >= int((datetime.now(UTC) - timedelta(days=5)).timestamp())
            ]
        if range_key == "YTD":
            candles = [
                c
                for c in candles
                if c["time"] >= int(datetime(datetime.now(UTC).year, 1, 1, tzinfo=UTC).timestamp())
            ]
        currency = "USD"
        source = "CoinGecko"
        interval = "provider-selected"
    else:
        ticker, info = common.ticker_info(asset["symbol"])
        period, interval = common.RANGES[range_key]
        frame = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            actions=False,
            timeout=10,
        )
        candles = []
        for timestamp, row in frame.iterrows():
            record = {
                name: common.number(row.get(column))
                for name, column in (
                    ("open", "Open"),
                    ("high", "High"),
                    ("low", "Low"),
                    ("close", "Close"),
                    ("volume", "Volume"),
                )
            }
            if any(
                record[name] is None or record[name] <= 0
                for name in ("open", "high", "low", "close")
            ):
                continue
            if timestamp.tzinfo is None:
                zone = info.get("exchangeTimezoneName")
                if not zone:
                    raise ValueError("History timezone unavailable")
                timestamp = timestamp.tz_localize(zone)
            if record["volume"] is not None and record["volume"] < 0:
                record["volume"] = None
            record["time"] = int(timestamp.timestamp())
            record["session"] = timestamp.strftime("%Y-%m-%d")
            candles.append(record)
        currency = info.get("currency")
        source = "Yahoo Finance"
    original_count = len(candles)
    candles = [
        row
        for row in candles
        if all(
            common.number(row.get(name)) is not None and row[name] > 0
            for name in ("open", "high", "low", "close")
        )
        and row["high"] >= max(row["open"], row["close"], row["low"])
        and row["low"] <= min(row["open"], row["close"], row["high"])
        and 0 < row["time"] < 253402300800
    ]
    invalid_count = original_count - len(candles)
    candles = sorted({row["time"]: row for row in candles}.values(), key=lambda row: row["time"])[
        -10000:
    ]
    return common.result(
        candles,
        source,
        "Unadjusted display OHLC; separate from adjusted portfolio history",
        currency=currency,
        instrument=asset,
        interval=interval,
        range=range_key,
        observed_at=datetime.fromtimestamp(candles[-1]["time"], UTC).isoformat()
        if candles
        else None,
        limitations=(["Volume unavailable"] if asset["asset_class"] == "crypto" else [])
        + (
            [f"Excluded {invalid_count} invalid provider bars; missing bars were not filled"]
            if invalid_count
            else []
        ),
    )
