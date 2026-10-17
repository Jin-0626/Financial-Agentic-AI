# US equity lists

US lists retain the broad Yahoo US equity universe, including OTC listings and
instruments Yahoo classifies as equities. There is no curated symbol basket or
hidden liquidity/market-cap restriction.

Yahoo's provider-side change and volume ordering can disagree with returned
regular-session quote fields. The backend therefore pages the entire provider
universe in stable symbol order, then filters, sorts, and paginates its normalized
quote snapshot. The same values are used for ranking and display. Missing values
sort last in either direction; prices are compared as exact decimal strings.

All views share a 60-second snapshot. Pages contain at most 250 securities, with
four workers using the existing global Yahoo request limit. At most 20,000
securities are accepted. Missing pages, changed provider totals, duplicate
identities, oversized universes, and rate limits fail explicitly; an incomplete
universe is never silently published as a complete ranking. Existing response
caches retain the last successful data with stale/error labels after failures.

Quote dates may differ, particularly for suspended or illiquid securities.
Rankings reflect returned regular-session quotes, not pre/post-market activity.
The page's observation timestamp is its oldest quote when every row has a date.
This is provider-listed coverage, not a guarantee of every US issuer.
