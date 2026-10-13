import { StrictMode, useState, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  useLocation,
  NavLink,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
const TracesPage = lazy(() =>
  import("./traces").then((module) => ({ default: module.TracesPage })),
);
import "./style.css";
import { Session, logout } from "./session";
import { ModulesPage } from "./modules";
import { Identity } from "./api";
import { desktop, openWorkspace } from "./desktop";
const workspaceLinks = [
  { path: "/terminal", label: "Terminal" },
  { path: "/portfolio", label: "Portfolio" },
  { path: "/research", label: "Research" },
  { path: "/traces", label: "Traces" },
  { path: "/modules", label: "Terminal modules" },
];
const TerminalPage = lazy(() =>
  import("./terminal").then((module) => ({ default: module.TerminalPage })),
);
import { TerminalProvider } from "./terminal/state";
import { InstrumentPalette } from "./terminal/palette";
function LegacyWorkspace({ type }: { type: "portfolio" | "research" }) {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  query.set("focus", type);
  query.set(
    type === "portfolio" ? "widget" : "research",
    type === "portfolio" ? "portfolio" : "1",
  );
  return <Navigate to={`/terminal?${query}`} replace />;
}
function App({ authenticated }: { authenticated: Identity | null }) {
  const location = useLocation();
  const currentWorkspace =
    workspaceLinks.find((link) => link.path === location.pathname)?.label ||
    "Portfolio";
  const [identity, setIdentity] = useState(() => ({
    org: authenticated?.org || localStorage.getItem("org") || "default-org",
    user: authenticated?.user || localStorage.getItem("user") || "local-user",
  }));
  const [draft, setDraft] = useState(identity);
  return (
    <TerminalProvider
      key={JSON.stringify([identity.org, identity.user])}
      identity={identity}
    >
      <div className="shell">
        <aside className="sidebar" aria-label="Workspace navigation">
          <div className="brand">
            <b>F</b>
            <span>
              RESEARCH
              <br />
              <small>FINANCIAL TERMINAL</small>
            </span>
          </div>
          <div className="nav-label">WORKSPACE</div>
          {workspaceLinks.map((link) => (
            <NavLink key={link.path} to={link.path}>
              {link.label}
            </NavLink>
          ))}
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
            FINANCIAL DEEP AGENTS
            <br />
            <span>Local workspace</span>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="terminal-heading">
              <span className="terminal-name">FINANCIAL</span>
              <span className="toolbar-divider">/</span>
              <span>{currentWorkspace}</span>
            </div>
            <InstrumentPalette />
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
              <Route path="/" element={<Navigate to="/terminal" replace />} />
              <Route
                path="/terminal"
                element={<TerminalPage identity={identity} />}
              />
              <Route
                path="/portfolio"
                element={<LegacyWorkspace type="portfolio" />}
              />
              <Route
                path="/research"
                element={<LegacyWorkspace type="research" />}
              />
              <Route
                path="/traces"
                element={
                  <TracesPage
                    key={JSON.stringify([identity.org, identity.user])}
                    identity={identity}
                  />
                }
              />
              <Route path="*" element={<Navigate to="/terminal" replace />} />
            </Routes>
          </Suspense>
        </div>
      </div>
    </TerminalProvider>
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
