import { useDesktopView } from "./view-settings";
import type { Screen } from "./bridge";
export const screenLabels: Record<Screen, string> = {
  dashboard: "Chart",
  markets: "Markets",
  watchlist: "Watchlists",
  crypto: "Crypto",
  portfolio: "Portfolio",
  research: "Research",
  news: "News",
  screener: "Screeners",
  options: "Options",
  macro: "Macro",
  calendar: "Calendar",
  backtesting: "Backtesting",
  traces: "Traces",
  settings: "Settings",
};
export const screens: Screen[] = [
  "dashboard",
  "markets",
  "watchlist",
  "crypto",
  "portfolio",
  "research",
  "news",
  "screener",
  "options",
  "macro",
  "calendar",
  "backtesting",
  "traces",
  "settings",
];
const paths: Record<Screen, string> = {
  dashboard: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  markets: "M3 19V5 M3 19h18 M6 15l4-5 4 3 6-8",
  watchlist: "M5 3h14v18l-7-4-7 4z",
  crypto: "M8 3v18 M12 3v18 M5 6h10a3 3 0 0 1 0 6H5 M5 12h11a3 3 0 0 1 0 6H5",
  portfolio: "M3 7h18v14H3z M8 7V3h8v4 M3 12h18 M10 12v3h4v-3",
  research: "M3 4h18v14H9l-6 3z M7 8h10 M7 12h7",
  news: "M4 3h16v18H4z M8 7h8 M8 11h8 M8 15h8",
  screener: "M3 4h18l-7 8v8l-4-2v-6z",
  options: "M3 12h18 M7 5v14 M17 5v14 M4 5h6 M14 19h6",
  macro: "M12 3v18 M3 12h18 M4 6l4 2 4-4 4 3 4-2 M4 18l4-3 4 2 4-3 4 2",
  calendar: "M3 5h18v16H3z M7 2v6 M17 2v6 M3 10h18 M7 14h2 M13 14h2",
  backtesting: "M3 18l5-5 4 3 8-11 M15 5h5v5 M3 21h18",
  traces: "M3 5h6 M9 5v14 M9 12h7 M16 12v7 M9 19h4 M16 19h5",
  settings:
    "M9 3h6l1 4 4 1v7l-4 1-1 5H9l-1-5-4-1V8l4-1z M9 12a3 3 0 1 0 6 0a3 3 0 1 0-6 0",
};
export function Navigation({
  screen,
  onNavigate,
}: {
  screen: Screen;
  onNavigate: (screen: Screen) => void;
}) {
  const { view, update } = useDesktopView();
  const primary: Screen[] = [
    "dashboard",
    "markets",
    "watchlist",
    "options",
    "portfolio",
    "calendar",
  ];
  const secondary: Screen[] = [
    "crypto",
    "news",
    "screener",
    "macro",
    "backtesting",
    "traces",
  ];
  const button = (item: Screen) => (
    <button
      key={item}
      className={item === screen ? "active" : ""}
      aria-pressed={item === screen}
      aria-label={item}
      title={screenLabels[item]}
      onClick={() => onNavigate(item)}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d={paths[item]} />
      </svg>
      <span>{screenLabels[item]}</span>
    </button>
  );
  return (
    <nav className="navigation-rail" aria-label="Workspace views">
      <div className="rail-brand">
        <span aria-label="Financial Research Terminal">F</span>
        <button
          aria-label={
            view.rail_collapsed ? "Expand navigation" : "Collapse navigation"
          }
          onClick={() => update({ rail_collapsed: !view.rail_collapsed })}
        >
          ☰
        </button>
      </div>
      {primary.map(button)}
      <details className="navigation-more" key={screen}>
        <summary title="More screens" data-active={secondary.includes(screen)}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 6h16 M4 12h16 M4 18h16" />
          </svg>
          <span>More</span>
        </summary>
        <div>{secondary.map(button)}</div>
      </details>
      <div className="navigation-bottom">
        {button("research")}
        {button("settings")}
      </div>
    </nav>
  );
}
