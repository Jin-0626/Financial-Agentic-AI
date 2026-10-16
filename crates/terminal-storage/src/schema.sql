CREATE TABLE app_state (
    namespace TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE watchlist (
    namespace TEXT NOT NULL REFERENCES app_state(namespace),
    symbol TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (namespace, symbol),
    UNIQUE (namespace, position)
);
