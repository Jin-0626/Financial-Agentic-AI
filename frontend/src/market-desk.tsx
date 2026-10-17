import { StockLabel } from "./stock-label";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { price, type Snapshot } from "./bridge";
import { useDesktopView } from "./view-settings";
import { Inspector } from "./inspector";
export function MarketDesk({
  snapshot,
  children,
}: {
  snapshot: Snapshot;
  children: ReactNode;
}) {
  const { view, update } = useDesktopView();
  const selected = snapshot.preferences.selected;
  const market = selected ? snapshot.quotes[selected] : null;
  const quote = market?.data;
  const inspector = view.inspector_open;
  const [inspectorWidth, setInspectorWidth] = useState(view.inspector_width);
  useEffect(
    () => setInspectorWidth(view.inspector_width),
    [view.inspector_width],
  );
  const resize = useRef<{ x: number; width: number } | null>(null);
  const inspectorId = useId();
  const clampWidth = (width: number) => Math.max(260, Math.min(420, width));
  const [wide, setWide] = useState(
    () => window.matchMedia("(min-width: 1200px)").matches,
  );
  const drawer = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1200px)");
    const change = () => setWide(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    if (!wide && inspector)
      drawer.current
        ?.querySelector<HTMLButtonElement>('[aria-label="Close inspector"]')
        ?.focus();
  }, [wide, inspector]);
  const close = () => {
    update({ inspector_open: false });
    trigger.current?.focus();
  };
  return (
    <div className="market-desk">
      <div className="market-desk-center">
        <div className="desk-heading">
          <span className="market-heading">
            <h2>
              {selected ? (
                <StockLabel code={selected} name={quote?.instrument.name} />
              ) : (
                "No instrument selected"
              )}
            </h2>
          </span>
          <div className="desk-quote" aria-label="Active instrument quote">
            <strong>{price(quote?.price)}</strong>
            <span
              className={
                quote?.change_percent == null
                  ? ""
                  : quote.change_percent.startsWith("-")
                    ? "negative"
                    : "positive"
              }
            >
              {quote?.change_percent == null
                ? "Change unavailable"
                : `${price(quote.change_percent)}%`}
            </span>
            <small>{quote?.currency ?? "Currency unavailable"}</small>
            {market?.stale && <small className="negative">STALE</small>}
            {market?.status === "unavailable" && <small>Unavailable</small>}
          </div>
          <button
            ref={trigger}
            className="inspector-open"
            aria-expanded={inspector}
            aria-controls={inspectorId}
            onClick={() => update({ inspector_open: !inspector })}
          >
            Instrument details
          </button>
        </div>
        {children}
      </div>
      {wide && inspector && (
        <div
          className="inspector-divider"
          role="separator"
          aria-label="Resize instrument inspector"
          aria-orientation="vertical"
          aria-valuemin={260}
          aria-valuemax={420}
          aria-valuenow={inspectorWidth}
          aria-controls={inspectorId}
          tabIndex={0}
          onDoubleClick={() => {
            setInspectorWidth(290);
            update({ inspector_width: 290 });
          }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            resize.current = { x: event.clientX, width: inspectorWidth };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (resize.current)
              setInspectorWidth(
                clampWidth(
                  resize.current.width + resize.current.x - event.clientX,
                ),
              );
          }}
          onPointerUp={(event) => {
            resize.current = null;
            update({ inspector_width: inspectorWidth });
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onLostPointerCapture={() => {
            resize.current = null;
          }}
          onPointerCancel={() => {
            resize.current = null;
          }}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            event.stopPropagation();
            const width =
              event.key === "Home"
                ? 260
                : event.key === "End"
                  ? 420
                  : clampWidth(
                      inspectorWidth + (event.key === "ArrowLeft" ? 10 : -10),
                    );
            setInspectorWidth(width);
            update({ inspector_width: width });
          }}
        />
      )}
      <div
        ref={drawer}
        id={inspectorId}
        role={!wide && inspector ? "dialog" : undefined}
        aria-modal={!wide && inspector ? true : undefined}
        aria-label={!wide && inspector ? "Instrument details" : undefined}
        style={wide ? { flexBasis: inspectorWidth } : undefined}
        className={`inspector-slot ${inspector ? "open" : "closed"}`}
        onKeyDown={(event) => {
          if (wide) return;
          if (event.key === "Escape") {
            event.stopPropagation();
            close();
          }
          if (event.key === "Tab") {
            const controls = Array.from(
              drawer.current?.querySelectorAll<HTMLElement>(
                'button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]',
              ) ?? [],
            ).filter((element) => element.getClientRects().length > 0);
            const first = controls[0],
              last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        {inspector && (
          <Inspector
            snapshot={snapshot}
            onClose={close}
            tab={view.inspector_tab}
            onTab={(inspector_tab) => update({ inspector_tab })}
          />
        )}
      </div>
      <nav className="information-tools" aria-label="Instrument tools">
        {(
          [
            ["quote", "Quote", "M4 4h16v16H4z M7 9h10 M7 13h6"],
            ["news", "News", "M4 3h16v18H4z M7 7h10 M7 11h10 M7 15h7"],
            ["calendar", "Calendar", "M3 5h18v16H3z M7 2v6 M17 2v6 M3 10h18"],
          ] as const
        ).map(([tab, label, path]) => (
          <button
            key={tab}
            aria-label={`Open ${label.toLowerCase()} inspector`}
            aria-pressed={inspector && view.inspector_tab === tab}
            title={label}
            onClick={() =>
              update({
                inspector_open: !inspector || view.inspector_tab !== tab,
                inspector_tab: tab,
              })
            }
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d={path} />
            </svg>
          </button>
        ))}
      </nav>
      {!wide && inspector && (
        <button
          className="inspector-backdrop"
          aria-label="Dismiss inspector"
          onClick={close}
        />
      )}
    </div>
  );
}
