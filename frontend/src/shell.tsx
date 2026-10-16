import { useDesktopView } from "./view-settings";
import { useEffect, type ReactNode } from "react";
import type { Screen } from "./bridge";
import { Navigation } from "./navigation";
export function Shell({
  screen,
  compact,
  onNavigate,
  header,
  toolbar,
  children,
  footer,
  overlay,
}: {
  screen: Screen;
  compact: boolean;
  onNavigate: (screen: Screen) => void;
  header: ReactNode;
  toolbar: ReactNode;
  children: ReactNode;
  footer: ReactNode;
  overlay: ReactNode;
}) {
  const { view } = useDesktopView();
  useEffect(() => {
    const selector =
      ".workspace-tools,.navigation-more,.watchlist-menu,.dock-menu,.browser-presets,.drawing-menu,.chart-style-menu";
    const close = (event: PointerEvent) => {
      for (const menu of document.querySelectorAll<HTMLDetailsElement>(
        selector,
      ))
        if (event.target instanceof Node && !menu.contains(event.target))
          menu.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.querySelector("dialog[open]"))
        return;
      const focused = document.activeElement;
      if (!(focused instanceof HTMLElement)) return;
      const menu = focused.closest<HTMLDetailsElement>(selector);
      if (menu?.open) {
        event.preventDefault();
        event.stopPropagation();
        menu.open = false;
        menu.querySelector<HTMLElement>(":scope > summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape, true);
    };
  }, []);
  return (
    <div className="app" data-rail-collapsed={view.rail_collapsed}>
      <Navigation screen={screen} onNavigate={onNavigate} />
      <div className="workspace" data-view={screen} data-compact={compact}>
        {header}
        {toolbar}
        <main className="workspace-content">{children}</main>
        {footer}
      </div>
      {overlay}
    </div>
  );
}
