import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity,
  api,
  Identity,
  researchStream,
  scope,
  Statements,
} from "./api";
type Message = {
  role: string;
  content: string;
  financial_statements?: Statements[];
  activity?: Activity[];
};
function StatementTables({ companies }: { companies: Statements[] }) {
  const [active, setActive] = useState("income_statement");
  return (
    <>
      {companies.map((company) => {
        const statement = company[active as keyof Statements] as Record<
          string,
          Record<string, unknown>
        >;
        const periods = Object.keys(statement || {})
          .sort()
          .reverse();
        const labels = Array.from(
          new Set(periods.flatMap((p) => Object.keys(statement[p]))),
        );
        return (
          <section className="panel" key={company.symbol}>
            <h2>Financial statements · {company.symbol}</h2>
            <p className="notice">
              {company.frequency} · {company.source} ·{" "}
              {company.currency || "Currency not supplied"} · Values as reported
            </p>
            <div className="tabs">
              {[
                ["income_statement", "Income statement"],
                ["balance_sheet", "Balance sheet"],
                ["cash_flow", "Cash flow"],
              ].map(([key, label]) => (
                <button
                  className={active === key ? "selected" : ""}
                  key={key}
                  onClick={() => setActive(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Line item</th>
                    {periods.map((p) => (
                      <th key={p}>{p}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {labels.map((label) => (
                    <tr key={label}>
                      <td>{label}</td>
                      {periods.map((p) => (
                        <td key={p}>
                          {statement[p][label] == null
                            ? "—"
                            : typeof statement[p][label] === "number"
                              ? Number(statement[p][label]).toLocaleString()
                              : String(statement[p][label])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!periods.length && <p>Statement unavailable from the provider.</p>}
          </section>
        );
      })}
    </>
  );
}
export function ResearchPage({ identity }: { identity: Identity }) {
  const [threads, setThreads] = useState<
    { thread_id: string; last_message?: string }[]
  >([]);
  const [thread, setThread] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activities, setActivities] = useState<Activity[]>([]);
  const [draft, setDraft] = useState("");
  const [budget, setBudget] = useState<{ tokens_used: number; max_tokens: number; steps_used: number; max_steps: number; missing_usage: number; phase?: string; reserved_tokens?: number; cache_hits?: number } | null>(null);
  const [todos, setTodos] = useState<{ task: string; status: string }[]>([]);
  const [interruptions, setInterruptions] = useState<
    { id: string; value: unknown }[]
  >([]);
  const controller = useRef<AbortController | null>(null);
  const statements = useRef<Statements[]>([]);
  const finished = useRef(false);
  const newThread = () => {
    setThread(`${identity.org}__${identity.user}__${crypto.randomUUID()}`);
    setMessages([]);
    setDraft("");
    setActivities([]);
    setInterruptions([]);
    setError("");
  };
  const loadThreads = () =>
    api<typeof threads>(`/threads?${scope(identity)}`)
      .then(setThreads)
      .catch((e) => setError(e.message));
  useEffect(() => {
    newThread();
    void loadThreads();
    return () => controller.current?.abort();
  }, [identity]);
  async function run(resume?: Record<string, unknown>) {
    if (busy || (!input.trim() && !resume)) return;
    const question = input;
    setBusy(true);
    setError("");
    setActivities([]);
    setDraft("");
    setTodos([]);
    setBudget(null);
    statements.current = [];
    finished.current = false;
    setInterruptions([]);
    controller.current = new AbortController();
    if (!resume) {
      setMessages((previous) => [
        ...previous,
        { role: "user", content: question },
      ]);
      setInput("");
    }
    try {
      await researchStream(
        {
          message: resume ? "" : question,
          thread_id: thread,
          user_id: identity.user,
          org_id: identity.org,
          response_schema: "fincept",
          ...(resume ? { resume } : {}),
        },
        (event) => {
          if (event.type === "budget") setBudget(event as unknown as NonNullable<typeof budget>);
          if (event.type === "token")
            setDraft((previous) => previous + String(event.content || ""));
          if (event.type === "tool_call")
            setActivities((previous) => [
              ...previous,
              {
                id: String(event.id),
                name: String(event.name),
                agent: event.agent ? String(event.agent) : undefined,
                status: "running",
              },
            ]);
          if (event.type === "tool_result")
            setActivities((previous) => {
              const result = {
                id: String(event.id),
                name: String(event.name),
                status: String(event.status || "complete"),
                output: String(event.output || ""),
              };
              return previous.some((a) => a.id === result.id)
                ? previous.map((a) =>
                    a.id === result.id ? { ...a, ...result } : a,
                  )
                : [...previous, result];
            });
          if (event.type === "financial_statements")
            statements.current = event.companies as Statements[];
          if (event.type === "interrupted") {
            finished.current = true;
            setInterruptions(event.interruptions as typeof interruptions);
            setDraft("");
          }
          if (event.type === "error") {
            finished.current = true;
            setError(String(event.message));
            setDraft("");
          }
          if (event.type === "done") {
            finished.current = true;
            setDraft("");
            setTodos((event.todos || []) as typeof todos);
            setMessages((previous) => [
              ...previous,
              {
                role: "assistant",
                content: String(event.result || ""),
                financial_statements: statements.current,
              },
            ]);
            void loadThreads();
          }
        },
        controller.current.signal,
      );
      if (!finished.current)
        setError(
          "The stream ended before the final report. Retry or reopen the conversation.",
        );
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main>
      <div className="page-title">
        <div>
          <span className="eyebrow">DEEP AGENTS</span>
          <h1>Research</h1>
        </div>
        <button disabled={busy} onClick={newThread}>
          + New conversation
        </button>
      </div>
      <div className="research-layout">
        <aside className="conversations">
          <h2>Conversations</h2>
          {threads.map((t) => (
            <div key={t.thread_id}>
              <button
                disabled={busy}
                className={thread === t.thread_id ? "selected" : ""}
                onClick={() => {
                  setThread(t.thread_id);
                  setError("");
                  setDraft("");
                  setActivities([]);
                  void api<{ messages: Message[] }>(
                    `/history/${encodeURIComponent(t.thread_id)}?${scope(identity)}`,
                  )
                    .then((d) => {
                      setMessages(d.messages);
                      setActivities(
                        [...d.messages]
                          .reverse()
                          .find((message) => message.role === "assistant")
                          ?.activity || [],
                      );
                    })
                    .catch((e) => setError(e.message));
                }}
              >
                {t.last_message || t.thread_id}
              </button>
              <button
                aria-label="Delete conversation"
                disabled={busy}
                onClick={() => {
                  void api(
                    `/threads/${encodeURIComponent(t.thread_id)}?${scope(identity)}`,
                    { method: "DELETE" },
                  )
                    .then(() => {
                      if (thread === t.thread_id) newThread();
                      void loadThreads();
                    })
                    .catch((e) => setError(e.message));
                }}
              >
                ×
              </button>
            </div>
          ))}
        </aside>
        <div className="chat">
          <div className="messages">
            {!messages.length && (
              <div className="empty">
                <h2>Research with specialist agents</h2>
                <p>
                  Ask about a company, financial statements, valuation or risk.
                </p>
              </div>
            )}
            {messages.map((m, i) => (
              <article key={i} className={`message ${m.role}`}>
                <span className="eyebrow">
                  {m.role === "user" ? "YOU" : "RESEARCH"}
                </span>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.content}
                </ReactMarkdown>
                {m.financial_statements && (
                  <StatementTables companies={m.financial_statements} />
                )}
              </article>
            ))}
            {draft && (
              <article className="message draft">
                <span className="eyebrow">WORKING DRAFT</span>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {draft}
                </ReactMarkdown>
              </article>
            )}
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            {interruptions.map((item) => (
              <div key={item.id} className="panel">
                <h2>Agent decision</h2>
                <pre>{JSON.stringify(item.value, null, 2)}</pre>
                <button
                  onClick={() =>
                    void run({
                      [item.id]: { decisions: [{ type: "approve" }] },
                    })
                  }
                >
                  Approve
                </button>
                <button
                  onClick={() =>
                    void run({
                      [item.id]: {
                        decisions: [
                          { type: "reject", message: "User rejected" },
                        ],
                      },
                    })
                  }
                >
                  Reject
                </button>
              </div>
            ))}
          </div>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
          >
            <textarea
              aria-label="Research question"
              placeholder="Research a company or market…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={busy}
            />
            {busy ? (
              <button
                type="button"
                onClick={() => {
                  controller.current?.abort();
                  setDraft("");
                }}
              >
                Stop
              </button>
            ) : (
              <button className="accent" disabled={!input.trim()}>
                Research →
              </button>
            )}
          </form>
        </div>
        <aside className="activity panel">
          <h2>Agent activity</h2>
          {busy && <p className="positive">Research running</p>}
          {activities.map((a) => (
            <div className="activity-item" key={a.id}>
              <b>{a.name === "task" ? `Subagent: ${a.agent || "specialist"}` : a.agent ? `${a.agent} · ${a.name}` : a.name}</b>
              <span className={a.status === "error" ? "negative" : ""}>
                {a.status}
              </span>
              {a.output && (
                <details>
                  <summary>Activity details</summary>
                  <p>{a.output}</p>
                </details>
              )}
            </div>
          ))}
          {budget && <p>Budget ({budget.phase || "research"}): {budget.tokens_used.toLocaleString()} / {budget.max_tokens.toLocaleString()} tokens · {budget.steps_used} / {budget.max_steps} tool steps · {budget.cache_hits || 0} reused calls{budget.missing_usage > 0 ? " · Some token usage unavailable" : ""}</p>}
          {todos.length > 0 && (
            <>
              <h2>Plan</h2>
              {todos.map((t, i) => (
                <p key={i}>
                  {t.status === "completed" ? "✓" : "○"} {t.task}
                </p>
              ))}
            </>
          )}
        </aside>
      </div>
    </main>
  );
}
