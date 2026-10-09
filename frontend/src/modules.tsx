import records from "./modules.json";
import { Link } from "react-router-dom";
export const modules = records.map((module) => ({
  ...module,
  path: module.id === "ai_chat" ? "/research" : `/${module.id}`,
  available: ["portfolio", "ai_chat"].includes(module.id),
}));
export function ModulesPage() {
  return (
    <main>
      <h1>Terminal modules</h1>
      <p className="notice">
        Sample. Available workflows remain under
        parity review; missing modules are not enabled.
      </p>
      {[1, 2, 3, 4, 5].map((stage) => (
        <section className="panel" key={stage}>
          <h2>Release {stage}</h2>
          <div className="module-grid">
            {modules
              .filter((m) => m.stage === stage)
              .map((m) => (
                <article key={m.id}>
                  <h3>{m.label}</h3>
                  <p>
                    {m.status} ·{" "}
                    {m.registered
                      ? "Sample screen"
                      : "Reachability under review"}
                  </p>
                  {m.available ? (
                    <Link to={m.path}>Open workspace</Link>
                  ) : (
                    <button disabled>Unavailable</button>
                  )}
                </article>
              ))}
          </div>
        </section>
      ))}
    </main>
  );
}
