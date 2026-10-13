import { useEffect, useMemo, useState } from "react";
import { api, Identity, scope } from "../../shared/api";

type TraceSummary = {
  id: string;
  thread_id: string;
  name: unknown;
  started_at: string;
  status: string;
  duration_ms: number;
  call_count: number;
  limited: boolean;
  token_count: number;
};

type Call = {
  id: string;
  parent_id: string | null;
  token_count: number;
  name: string;
  kind: string;
  status: string;
  started_at: string;
  duration_ms: number;
  input: unknown;
  output: unknown;
  error?: string;
};

type Trace = TraceSummary & { calls: Call[]; error?: string };

const duration = (ms: number) =>
  ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;

const formatTokens = (tokens: number = 0) =>
  new Intl.NumberFormat().format(tokens);

const display = (value: unknown) =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);

export function TracesPage({ identity }: { identity: Identity }) {
  const [rows, setRows] = useState<TraceSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [callId, setCallId] = useState<string | null>(null);
  const [filter, setFilter] = useState("tool");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [live, setLive] = useState(true);

  // Configurable token budget threshold for alerts/progress indicator
  const [tokenBudget, setTokenBudget] = useState(100000);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;

    async function load() {
      try {
        const result = await api<{ traces: TraceSummary[] }>(
          `/traces?${scope(identity)}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setRows(result.traces);

        if (selected && !result.traces.some((row) => row.id === selected)) {
          setSelected(result.traces[0]?.id || null);
          setTrace(null);
          setError("");
          return;
        }

        const detail = selected
          ? await api<Trace>(
              `/traces/${encodeURIComponent(selected)}?${scope(identity)}`,
              { signal: controller.signal },
            )
          : null;

        if (controller.signal.aborted) return;
        setRows(result.traces);
        setTrace(detail);
        setSelected((current) => current || result.traces[0]?.id || null);
        setError("");
      } catch (failure) {
        if (!controller.signal.aborted) setError(String(failure));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (live) timer = setTimeout(() => void load(), 3000);
        }
      }
    }

    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [identity.org, identity.user, selected, refresh, live]);

  const calls = trace?.calls || [];

  const visible = calls.filter(
    (call) =>
      (filter === "all" || call.kind === filter) &&
      call.name.toLowerCase().includes(query.toLowerCase()),
  );

  const selectedCall = visible.find((call) => call.id === callId) || visible[0];

  // Token metrics computation
  const tokenMetrics = useMemo(() => {
    const totalTokens =
      trace?.token_count ??
      calls.reduce((sum, call) => sum + (call.token_count || 0), 0);

    const byKind: Record<string, number> = {};
    let maxConsumer: Call | null = null;

    for (const call of calls) {
      const count = call.token_count || 0;
      byKind[call.kind] = (byKind[call.kind] || 0) + count;
      if (!maxConsumer || count > (maxConsumer.token_count || 0)) {
        maxConsumer = call;
      }
    }

    const percentOfBudget =
      tokenBudget > 0 ? Math.min(100, (totalTokens / tokenBudget) * 100) : 0;
    const isExceeded = totalTokens > tokenBudget;

    return { totalTokens, byKind, maxConsumer, percentOfBudget, isExceeded };
  }, [trace, calls, tokenBudget]);

  function depth(call: Call) {
    let parent = call.parent_id;
    const seen = new Set<string>();
    while (parent && !seen.has(parent) && seen.size < 12) {
      seen.add(parent);
      parent = calls.find((entry) => entry.id === parent)?.parent_id || null;
    }
    return seen.size;
  }

  return (
    <main>
      <div className="page-title">
        <div>
          <span className="eyebrow">DEEP AGENTS</span>
          <h1>Traces</h1>
        </div>
        <button onClick={() => setRefresh((value) => value + 1)}>
          Refresh traces
        </button>
      </div>

      <p className="muted">
        Inspect research runs, nested tool calls, model responses, and timing.
        Recent traces are held locally until the backend restarts; large
        payloads are marked as truncated.
      </p>

      <label className="trace-auto-refresh">
        <input
          type="checkbox"
          checked={live}
          onChange={(event) => setLive(event.target.checked)}
        />{" "}
        Auto-refresh every 3 seconds
      </label>

      {error && (
        <p role="alert" className="negative">
          {error}
        </p>
      )}

      {loading && <p role="status">Loading traces…</p>}

      {!loading && !rows.length && !error && (
        <p>No traces yet. Start a research request to record its calls.</p>
      )}

      <div className="trace-layout">
        {/* Runs List */}
        <section className="panel trace-runs" aria-label="Research runs">
          <h2>Runs</h2>
          {rows.map((row) => (
            <button
              key={row.id}
              className={selected === row.id ? "selected" : ""}
              onClick={() => {
                setSelected(row.id);
                setTrace(null);
                setCallId(null);
              }}
            >
              <strong>{display(row.name)}</strong>
              <span>
                {row.status} · {duration(row.duration_ms)} · {row.call_count}{" "}
                calls
              </span>
              <span>
                ⚡ <strong>{formatTokens(row.token_count)}</strong> tokens
              </span>
              <small>{new Date(row.started_at).toLocaleString()}</small>
            </button>
          ))}
        </section>

        {/* Inspector Panel */}
        <section className="panel trace-inspector" aria-label="Trace inspector">
          {!trace ? (
            <p>Select a run to inspect its calls.</p>
          ) : (
            <>
              <h2>{display(trace.name)}</h2>
              <p>
                {trace.status} · {duration(trace.duration_ms)} · Thread:{" "}
                {trace.thread_id}
              </p>

              {/* Token Usage Monitor Widget */}
              <div
                className="token-monitor"
                style={{
                  border: "1px solid var(--border, #e0e0e0)",
                  borderRadius: "8px",
                  padding: "16px",
                  margin: "16px 0",
                  backgroundColor: "var(--surface, #fafafa)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <h3 style={{ margin: 0 }}>Token Usage Monitor</h3>
                  <label style={{ fontSize: "12px" }}>
                    Budget:{" "}
                    <input
                      type="number"
                      step="1000"
                      value={tokenBudget}
                      onChange={(e) =>
                        setTokenBudget(Number(e.target.value) || 0)
                      }
                      style={{ width: "90px", marginLeft: "4px" }}
                    />
                  </label>
                </div>

                <div style={{ margin: "8px 0" }}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "13px",
                      marginBottom: "4px",
                    }}
                  >
                    <span>
                      <strong>{formatTokens(tokenMetrics.totalTokens)}</strong>{" "}
                      / {formatTokens(tokenBudget)} tokens used
                    </span>
                    <span
                      style={{
                        color: tokenMetrics.isExceeded
                          ? "crimson"
                          : "var(--text-muted, #666)",
                      }}
                    >
                      {tokenMetrics.percentOfBudget.toFixed(1)}%
                    </span>
                  </div>

                  {/* Meter Bar */}
                  <div
                    style={{
                      height: "8px",
                      borderRadius: "4px",
                      backgroundColor: "#e0e0e0",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        height: "100%",
                        width: `${tokenMetrics.percentOfBudget}%`,
                        backgroundColor: tokenMetrics.isExceeded
                          ? "crimson"
                          : tokenMetrics.percentOfBudget > 80
                            ? "#e67e22"
                            : "#27ae60",
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                </div>

                {/* Sub-breakdowns */}
                <div
                  style={{
                    display: "flex",
                    gap: "16px",
                    flexWrap: "wrap",
                    fontSize: "12px",
                    marginTop: "10px",
                  }}
                >
                  {Object.entries(tokenMetrics.byKind).map(([kind, count]) => (
                    <span key={kind}>
                      <strong>{kind}:</strong> {formatTokens(count)} tokens (
                      {tokenMetrics.totalTokens > 0
                        ? Math.round((count / tokenMetrics.totalTokens) * 100)
                        : 0}
                      %)
                    </span>
                  ))}
                  {tokenMetrics.maxConsumer && (
                    <span>
                      <strong>Top consumer:</strong>{" "}
                      {tokenMetrics.maxConsumer.name} (
                      {formatTokens(tokenMetrics.maxConsumer.token_count)})
                    </span>
                  )}
                </div>
              </div>

              {trace.error && <p className="negative">{trace.error}</p>}
              {trace.limited && (
                <p role="status">
                  Recording limit reached. Some calls or payloads are omitted.
                </p>
              )}

              {/* Filters */}
              <div className="commandbar">
                <label>
                  Call type{" "}
                  <select
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                  >
                    <option value="tool">Tools</option>
                    <option value="model">Models</option>
                    <option value="all">All calls</option>
                  </select>
                </label>
                <input
                  aria-label="Filter calls"
                  placeholder="Filter by name"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>

              {/* Calls & Details */}
              <div className="trace-call-layout">
                <div className="trace-calls" aria-label="Call tree">
                  {visible.length === 0 && <p>No matching calls.</p>}
                  {visible.map((call) => (
                    <button
                      key={call.id}
                      className={selectedCall?.id === call.id ? "selected" : ""}
                      style={{ paddingLeft: `${12 + depth(call) * 12}px` }}
                      onClick={() => setCallId(call.id)}
                    >
                      <strong>{call.name}</strong>
                      <span>
                        {call.kind} · {call.status} ·{" "}
                        {duration(call.duration_ms)}
                      </span>
                      <small style={{ opacity: 0.85 }}>
                        Tokens: {formatTokens(call.token_count || 0)}
                      </small>
                    </button>
                  ))}
                </div>

                {selectedCall && (
                  <div className="trace-detail" aria-label="Call details">
                    <h3>{selectedCall.name}</h3>
                    <p>
                      {selectedCall.status} ·{" "}
                      {duration(selectedCall.duration_ms)} ·{" "}
                      <strong>
                        {formatTokens(selectedCall.token_count || 0)} tokens
                      </strong>{" "}
                      · {new Date(selectedCall.started_at).toLocaleString()}
                    </p>
                    <small>Call ID: {selectedCall.id}</small>

                    {selectedCall.error && (
                      <p role="alert" className="negative">
                        {selectedCall.error}
                      </p>
                    )}

                    <h3>Input</h3>
                    <pre>
                      {display(selectedCall.input) || "No input recorded"}
                    </pre>

                    <h3>Output</h3>
                    <pre>
                      {selectedCall.status === "running"
                        ? "Waiting for output…"
                        : display(selectedCall.output) || "No output recorded"}
                    </pre>
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}
