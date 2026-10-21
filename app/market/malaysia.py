"""Paginated Malaysian provider equities; tracked identifiers are legacy transport only."""

import os
import re
from app.market import common, us

DEFAULT_SYMBOLS = (
    "5326.KL",
    "1155.KL",
    "1295.KL",
    "1023.KL",
    "5347.KL",
    "5225.KL",
    "5819.KL",
    "4863.KL",
    "6947.KL",
    "6888.KL",
    "6033.KL",
    "5183.KL",
    "5285.KL",
    "5398.KL",
    "5296.KL",
    "3816.KL",
)


def symbols():
    configured = os.getenv("MY_EQUITY_SYMBOLS")
    values = DEFAULT_SYMBOLS if configured is None else re.split(r"[,;\s]+", configured.strip())
    result = tuple(dict.fromkeys(common.symbol(value.removeprefix("yahoo:")) for value in values))
    if not 1 <= len(result) <= 128 or any(not value.endswith(".KL") for value in result):
        raise ValueError("MY_EQUITY_SYMBOLS requires 1–128 exact Yahoo .KL identifiers")
    return result


universe = us.EquitySnapshot("MY")


def screener(sector, min_cap, min_change, min_volume, sort, descending, offset, limit):
    return us.screen_universe(
        universe, "MY", sector, min_cap, min_change, min_volume, sort, descending, offset, limit
    )
