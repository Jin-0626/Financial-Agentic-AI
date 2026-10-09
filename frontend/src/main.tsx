import { StrictMode, useState, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
const PortfolioPage = lazy(() =>
  import("./portfolio").then((module) => ({ default: module.PortfolioPage })),
);
const ResearchPage = lazy(() =>
  import("./research").then((module) => ({ default: module.ResearchPage })),
);
import "./style.css";
import { Session, logout } from "./session";
import { ModulesPage, modules } from "./modules";
import { Identity } from "./api";
import { desktop, openWorkspace } from "./desktop";
function App({ authenticated }: { authenticated: Identity | null }) {
  const [identity, setIdentity] = useState(() => ({
    org: authenticated?.org || localStorage.getItem("org") || "default-org",
    user: authenticated?.user || localStorage.getItem("user") || "local-user",
  }));
  const [draft, setDraft] = useState(identity);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <b>F</b>
          <span>
            RESEARCH
            <br />
            <small>FINANCIAL TERMINAL</small>
          </span>
        </div>
        <div className="nav-label">WORKSPACE</div>
        {modules
          .filter((m) => m.available)
          .map((m) => (
            <NavLink key={m.id} to={m.path}>
              {m.id === "ai_chat" ? "Research" : m.label}
            </NavLink>
          ))}
        <NavLink to="/modules">Terminal modules</NavLink>
        {desktop && (
          <button
            onClick={() =>
              void openWorkspace("portfolio").catch((error) =>
                window.alert(error),
              )
            }
          >
            Portfolio window
          </button>
        )}
        {desktop && (
          <button
            onClick={() =>
              void openWorkspace("research").catch((error) =>
                window.alert(error),
              )
            }
          >
            Research window
          </button>
        )}
        <div className="sidebar-foot">
          DEEP AGENTS + RUST
          <br />
          <span>Local workspace</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span className="live-dot" /> FINANCIAL RESEARCH TERMINAL
          {authenticated ? (
            <div>
              {identity.org} / {identity.user}{" "}
              <button onClick={() => void logout()}>Logout</button>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                localStorage.setItem("org", draft.org);
                localStorage.setItem("user", draft.user);
                setIdentity(draft);
              }}
            >
              <input
                aria-label="Organization"
                value={draft.org}
                onChange={(e) => setDraft({ ...draft, org: e.target.value })}
              />
              <input
                aria-label="User"
                value={draft.user}
                onChange={(e) => setDraft({ ...draft, user: e.target.value })}
              />
              <button>Switch workspace</button>
            </form>
          )}
        </header>
        <Suspense fallback={<main>Loading workspace…</main>}>
          <Routes>
            <Route path="/modules" element={<ModulesPage />} />
            <Route path="/" element={<Navigate to="/portfolio" replace />} />
            <Route
              path="/portfolio"
              element={
                <PortfolioPage
                  key={JSON.stringify([identity.org, identity.user])}
                  identity={identity}
                />
              }
            />
            <Route
              path="/research"
              element={
                <ResearchPage
                  key={JSON.stringify([identity.org, identity.user])}
                  identity={identity}
                />
              }
            />
            <Route path="*" element={<Navigate to="/portfolio" replace />} />
          </Routes>
        </Suspense>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Session>
        {(authenticated) => <App authenticated={authenticated} />}
      </Session>
    </BrowserRouter>
  </StrictMode>,
);
