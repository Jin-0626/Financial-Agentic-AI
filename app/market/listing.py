"""Shared ordering for HTTP market tables; desktop ranking executes in Rust."""

from decimal import Decimal
from typing import Any, Literal

Sort = Literal["symbol", "price", "market_cap", "change_percent", "volume"]
Row = dict[str, Any]


def page_rows(
    rows: list[Row],
    sector: str | None,
    min_cap: float | None,
    min_change: float | None,
    min_volume: int | None,
    sort: Sort,
    descending: bool,
    offset: int,
    limit: int,
) -> tuple[list[Row], int]:
    """Filter before ranking and paging, with stable ties and missing values last."""
    filters = (("market_cap", min_cap), ("change_percent", min_change), ("volume", min_volume))
    filtered = [
        row
        for row in rows
        if (not sector or (row.get("sector") or "").casefold() == sector.casefold())
        and all(
            minimum is None or (row.get(field) is not None and row[field] >= minimum)
            for field, minimum in filters
        )
    ]

    def value(row: Row) -> str | Decimal | float:
        if sort == "symbol":
            return row["instrument"]["symbol"]
        if sort == "price":
            return Decimal(row[sort])
        return row[sort]

    present = [row for row in filtered if sort == "symbol" or row.get(sort) is not None]
    absent = [row for row in filtered if sort != "symbol" and row.get(sort) is None]

    def identity(row):
        return row["instrument"]["id"]

    present.sort(key=identity)
    present.sort(key=value, reverse=descending)
    absent.sort(key=identity)
    return (present + absent)[offset : offset + limit], len(filtered)
