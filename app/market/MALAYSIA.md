# Malaysian equity set

MY equity tables and overview rankings use live Yahoo quotes for the 16 exact
identifiers in `malaysia.DEFAULT_SYMBOLS`. Yahoo's screener currently returns an
empty set for both the Malaysian region and KLSE exchange, even though direct
Malaysian quotes work. US tables retain the existing Yahoo screener.

These are **tracked-set rankings**, not all Bursa listings or a claim of current
KLCI membership. Names, prices, volume and market caps come from provider quotes.
Missing fields remain unavailable. Missing quotes reduce disclosed coverage;
complete failure uses the existing stale/unavailable response pipeline.

Override the set before backend startup using exact Yahoo identifiers:

```powershell
$env:MY_EQUITY_SYMBOLS = '5326.KL,1155.KL,1295.KL,1023.KL'
```

At most 128 distinct `.KL` identifiers are accepted. Exchange suffixes are never
inferred. A shared 60-second snapshot feeds volume/change rankings and the MY
table, with four quote workers using the existing global Yahoo concurrency limit.
Filtering, sorting and pagination operate across the available tracked snapshot;
missing sort values stay last in either direction. The observation timestamp is
the oldest quote on the returned page when every row has a timestamp. Quotes may
be delayed and are not synchronized market snapshots.

Restart the backend after changing the set. No broker account, dependency or
additional data provider is required.
