import pandas as pd
import ta
import yfinance as yf

from src.schemas.report import LiquidityProfile


def fetch_klse_telemetry(stock_code: str) -> LiquidityProfile:
    ticker = f"{stock_code}.KL" if not stock_code.endswith(".KL") else stock_code
    df = yf.download(ticker, period="6mo", interval="1d", progress=False)

    if df.empty:
        raise ValueError(f"No market data found on KLSE for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    rsi = ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1]
    latest_close = float(df["Close"].iloc[-1])
    adv_30 = int(df["Volume"].tail(30).mean())
    turnover_val = float((df["Close"] * df["Volume"]).tail(30).mean())

    status = (
        "High"
        if turnover_val > 1_000_000
        else "Moderate"
        if turnover_val > 200_000
        else "Illiquid/Caution"
    )

    return LiquidityProfile(
        current_price_myr=round(latest_close, 3),
        rsi_14=round(float(rsi), 2) if not pd.isna(rsi) else None,
        adv_30d=adv_30,
        turnover_30d_myr=round(turnover_val, 2),
        liquidity_status=status,
    )
