import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";

export type ResearchRun = {
  id: string;
  thread: string;
  message?: string;
  content: string;
  reasoning: string;
  activity: { type: string; name?: string; output?: string; status?: string }[];
  running: boolean;
  error: string | null;
};
type Thread = { thread_id: string; last_message: string | null };
type History = {
  messages: {
    role: string;
    content: string;
    reasoning?: string;
    activity?: { name: string; status?: string; output?: string }[];
  }[];
};
// Both values originate from the identity-scoped native session, never search input.
export function sameResearchThread(a: string, b: string): boolean {
  return (
    a === b ||
    (!a.includes("__") && b.endsWith(`__${a}`)) ||
    (!b.includes("__") && a.endsWith(`__${b}`))
  );
}
export function researchThreads(
  threads: Thread[],
  runs: ResearchRun[],
): Thread[] {
  const result = threads.map((row) => ({ ...row }));
  for (const run of runs) {
    const existing = result.find((row) =>
      sameResearchThread(row.thread_id, run.thread),
    );
    if (!existing)
      result.push({ thread_id: run.thread, last_message: run.message || null });
    else if (!existing.last_message && run.message)
      existing.last_message = run.message;
  }
  return result;
}
export function ResearchHistoryScreen() {
  const [threads, setThreads] = useState<Thread[]>([]),
    [thread, setThread] = useState("");
  const [runs, setRuns] = useState<ResearchRun[]>([]),
    [drafts, setDrafts] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<History | null>(null),
    [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState<string | null>(null),
    [sending, setSending] = useState(false),
    [revision, setRevision] = useState(0);
  const alive = useRef(false),
    mutation = useRef(0),
    submitting = useRef(false),
    completed = useRef(new Set<string>());
  const touched = useRef(false);
  const options = researchThreads(threads, runs);
  const run = runs
    .filter((value) => sameResearchThread(value.thread, thread))
    .at(-1);
  const message = drafts[thread] || "";
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    void invoke<Thread[]>("terminal_service", { request: { type: "threads" } })
      .then((value) => {
        if (!active) return;
        setThreads((previous) => [
          ...value,
          ...previous.filter(
            (row) =>
              !row.thread_id.includes("__") &&
              !value.some((saved) =>
                sameResearchThread(saved.thread_id, row.thread_id),
              ),
          ),
        ]);
        setThread((current) => current || value[0]?.thread_id || "");
      })
      .catch((error) => active && setError(String(error)));
    return () => {
      active = false;
    };
  }, [revision]);
  useEffect(() => {
    let active = true;
    setHistory(null);
    setHistoryLoading(Boolean(thread));
    if (thread)
      void invoke<History>("terminal_service", {
        request: { type: "history", thread },
      })
        .then((value) => {
          if (active) {
            setHistory(value);
            setHistoryLoading(false);
          }
        })
        .catch((error) => {
          if (active) {
            setError(String(error));
            setHistoryLoading(false);
          }
        });
    return () => {
      active = false;
    };
  }, [thread, revision]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const ticket = mutation.current;
      try {
        const value = await invoke<ResearchRun[]>("terminal_research", {
          action: { type: "list" },
        });
        if (active && ticket === mutation.current) {
          setRuns(value);
          const latest = value.find((value) => value.running) || value.at(-1);
          if (!touched.current && latest) setThread(latest.thread);
        }
      } catch (error) {
        if (active && ticket === mutation.current) setError(String(error));
      }
      if (active) timer = setTimeout(load, 1000);
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (run && !run.running && !completed.current.has(run.id)) {
      completed.current.add(run.id);
      setRevision((value) => value + 1);
    }
  }, [run?.id, run?.running]);
  const select = (id: string) => {
    touched.current = true;
    setThread(id);
    setError(null);
  };
  const tail = history?.messages.at(-1);
  const responseSaved = Boolean(
    run &&
    !run.running &&
    tail?.role === "assistant" &&
    tail.content === run.content &&
    run.content,
  );
  const questionSaved = Boolean(
    run?.message &&
    history?.messages.some(
      (item) => item.role === "user" && item.content === run.message,
    ),
  );
  return (
    <section className="research-workspace" aria-label="Research workspace">
      <aside className="research-threads">
        <h3>Research threads</h3>
        <div className="feature-toolbar">
          <select
            aria-label="Research thread"
            value={
              options.find((row) => sameResearchThread(row.thread_id, thread))
                ?.thread_id || thread
            }
            onChange={(event) => select(event.target.value)}
          >
            <option value="">Choose research thread</option>
            {options.map((row) => (
              <option key={row.thread_id} value={row.thread_id}>
                {row.last_message?.slice(0, 70) || row.thread_id}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => {
              const id = crypto.randomUUID();
              select(id);
              setThreads((previous) => [
                ...previous,
                { thread_id: id, last_message: "New research" },
              ]);
            }}
          >
            New research
          </button>
          <button
            type="button"
            onClick={() => {
              setError(null);
              setRevision((value) => value + 1);
            }}
          >
            Refresh research
          </button>
          {run?.running && (
            <button
              type="button"
              onClick={() => {
                mutation.current++;
                void invoke<ResearchRun[]>("terminal_research", {
                  action: { type: "cancel", id: run.id },
                })
                  .then((value) => {
                    if (alive.current) {
                      mutation.current++;
                      setRuns((previous) =>
                        previous.map(
                          (item) =>
                            value.find((next) => next.id === item.id) || item,
                        ),
                      );
                    }
                  })
                  .catch((error) => alive.current && setError(String(error)));
              }}
            >
              Cancel research
            </button>
          )}
        </div>
        <div className="thread-list">
          {options.map((row) => (
            <button
              type="button"
              key={row.thread_id}
              aria-pressed={sameResearchThread(thread, row.thread_id)}
              onClick={() => select(row.thread_id)}
            >
              {row.last_message?.slice(0, 70) || row.thread_id}
            </button>
          ))}
        </div>
      </aside>
      <div className="research-conversation">
        {error && <p role="alert">{error}</p>}
        {historyLoading && <p role="status">Loading research history…</p>}
        {!thread && !runs.length && (
          <div className="empty">
            Start a new research question or select a saved thread.
          </div>
        )}
        {history?.messages.map((item, index) => (
          <article className="research-message" key={index}>
            <h3>{item.role.toUpperCase()}</h3>
            <div className="message-content">{item.content}</div>
            {item.reasoning && (
              <details>
                <summary>Reasoning</summary>
                <p>{item.reasoning}</p>
              </details>
            )}
            {item.activity?.map((activity, i) => (
              <details key={i}>
                <summary>
                  {activity.name} · {activity.status}
                </summary>
                <pre>{activity.output}</pre>
              </details>
            ))}
          </article>
        ))}
        {run?.message && !questionSaved && (
          <article className="research-message" aria-label="Research question">
            <h3>USER</h3>
            <div className="message-content">{run.message}</div>
          </article>
        )}
        {run && (!responseSaved || run.error) && (
          <article
            className="research-message"
            aria-label="Research response"
            aria-busy={run.running}
          >
            <h3>{run.running ? "RESEARCH RUNNING" : "RESEARCH RESPONSE"}</h3>
            <div className="message-content">
              {run.content || (run.running ? "Research is starting…" : "")}
            </div>
            {run.error && (
              <p role="alert" className="negative">
                {run.error}
              </p>
            )}
            {run.reasoning && (
              <details>
                <summary>Reasoning</summary>
                <p>{run.reasoning}</p>
              </details>
            )}
            <details className="research-activity">
              <summary>Activity · {run.activity.length}</summary>
              {run.activity.map((activity, index) => (
                <details key={index}>
                  <summary>
                    {activity.name} · {activity.status ?? activity.type}
                  </summary>
                  <pre>{activity.output}</pre>
                </details>
              ))}
            </details>
          </article>
        )}
        <form
          className="research-compose"
          onSubmit={(event) => {
            event.preventDefault();
            if (submitting.current || run?.running || !message.trim()) return;
            const id = crypto.randomUUID(),
              selectedThread = thread || crypto.randomUUID(),
              submitted = message;
            touched.current = true;
            submitting.current = true;
            setSending(true);
            setThread(selectedThread);
            setThreads((previous) =>
              previous.some((row) =>
                sameResearchThread(row.thread_id, selectedThread),
              )
                ? previous
                : [
                    ...previous,
                    { thread_id: selectedThread, last_message: submitted },
                  ],
            );
            setError(null);
            mutation.current++;
            setDrafts((previous) => ({
              ...previous,
              [selectedThread]: submitted,
            }));
            void invoke<ResearchRun[]>("terminal_research", {
              action: {
                type: "start",
                id,
                request: { message: submitted, thread_id: selectedThread },
              },
            })
              .then((value) => {
                if (!alive.current) return;
                mutation.current++;
                setRuns((previous) => [
                  ...previous.filter(
                    (item) => !value.some((next) => next.id === item.id),
                  ),
                  ...value,
                ]);
                setDrafts((previous) =>
                  previous[selectedThread] === submitted
                    ? {
                        ...previous,
                        [selectedThread]: "",
                        ...(thread === "" ? { "": "" } : {}),
                      }
                    : previous,
                );
              })
              .catch((error) => alive.current && setError(String(error)))
              .finally(() => {
                submitting.current = false;
                if (alive.current) setSending(false);
              });
          }}
        >
          <label htmlFor="research-message">Research message</label>
          <textarea
            id="research-message"
            aria-label="Research message"
            value={message}
            maxLength={32000}
            onChange={(event) =>
              setDrafts((previous) => ({
                ...previous,
                [thread]: event.target.value,
              }))
            }
            placeholder="Ask a research question. Only this text is sent."
            rows={4}
          />
          <button disabled={!message.trim() || sending || run?.running}>
            {sending ? "Starting research…" : "Send research"}
          </button>
        </form>
      </div>
    </section>
  );
}
