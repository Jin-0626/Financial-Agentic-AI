import json

import pandas as pd
import ta
import yfinance as yf

from agents.schemas.report import LiquidityProfile


def fetch_klse_telemetry(stock_code: str) -> LiquidityProfile:
    ticker = f"{stock_code}.KL" if not stock_code.endswith(".KL") else stock_code
    df = yf.download(ticker, period="6mo", interval="1d", progress=False)

    if df.empty:
        raise ValueError(f"No market data found on KLSE for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    usable = df.dropna(subset=["Close", "Volume"]).copy()
    if usable.empty:
        raise ValueError(f"No usable market data found on KLSE for {ticker}")

    rsi_series = ta.momentum.RSIIndicator(usable["Close"], window=14).rsi().dropna()
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else None
    latest_close = float(usable["Close"].iloc[-1])
    adv_30 = int(usable["Volume"].tail(30).mean())
    turnover_val = float((usable["Close"] * usable["Volume"]).tail(30).mean())

    status = (
        "High"
        if turnover_val > 1_000_000
        else "Moderate"
        if turnover_val > 200_000
        else "Illiquid/Caution"
    )

    return LiquidityProfile(
        current_price_myr=round(latest_close, 3),
        rsi_14=round(rsi, 2) if rsi is not None and not pd.isna(rsi) else None,
        adv_30d=adv_30,
        turnover_30d_myr=round(turnover_val, 2),
        liquidity_status=status,
    )


def fetch_klse_market_snapshot(stock_code: str) -> str:
    """Return a compact structured KLSE market snapshot for agent consumption."""
    ticker = f"{stock_code}.KL" if not stock_code.endswith(".KL") else stock_code
    try:
        telemetry = fetch_klse_telemetry(stock_code)
    except Exception as exc:  # noqa: BLE001 - yfinance transport/cache exceptions vary.
        return json.dumps(
            {
                "ok": False,
                "source": "klse_yfinance",
                "ticker": ticker,
                "error_type": type(exc).__name__,
                "message": "KLSE market data unavailable for this run.",
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "ok": True,
            "source": "klse_yfinance",
            "ticker": ticker,
            "current_price_myr": telemetry.current_price_myr,
            "rsi_14": telemetry.rsi_14,
            "adv_30d": telemetry.adv_30d,
            "turnover_30d_myr": telemetry.turnover_30d_myr,
            "liquidity_status": telemetry.liquidity_status,
        },
        ensure_ascii=False,
    )
