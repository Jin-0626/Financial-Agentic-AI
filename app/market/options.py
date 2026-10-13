import logging
from app.market import common
from app.market import transport

logger = logging.getLogger(__name__)

@transport.yahoo_bound
def options(key, expiry=None):
    asset = common.instrument(key)
    if asset["asset_class"] == "crypto" or asset["symbol"].endswith(".KL"):
        return {
            **common.result(
                [],
                "Yahoo Finance",
                "No supported listed-option coverage for this instrument",
            ),
            "status": "unavailable",
        }
    ticker = transport.yf.Ticker(asset["symbol"])
    expiries = list(ticker.options)
    if not expiries:
        return {
            **common.result([], "Yahoo Finance", "Provider returned no option expiries"),
            "status": "unavailable",
        }
    selected = expiry or expiries[0]
    if selected not in expiries:
        raise ValueError("Expiry is not available for this instrument")
    chain = ticker.option_chain(selected)
    mapping = {
        "contract": "contractSymbol",
        "strike": "strike",
        "bid": "bid",
        "ask": "ask",
        "last": "lastPrice",
        "volume": "volume",
        "open_interest": "openInterest",
        "implied_volatility": "impliedVolatility",
    }

    def records(frame):
        return [
            {
                **{
                    key: (
                        str(row[field])
                        if key == "contract"
                        else common.decimal(row.get(field))
                    )
                    for key, field in mapping.items()
                },
                "itm": bool(row.get("inTheMoney", False)),
                "observed_at": row["lastTradeDate"].isoformat()
                if hasattr(row.get("lastTradeDate"), "isoformat")
                else None,
            }
            for _, row in frame.head(1000).iterrows()
        ]

    return common.result(
        {
            "calls": records(chain.calls),
            "puts": records(chain.puts),
            "expiries": expiries,
            "expiry": selected,
        },
        "Yahoo Finance",
        "Listed options where supplied; quotes may be delayed",
        instrument=asset,
    )

