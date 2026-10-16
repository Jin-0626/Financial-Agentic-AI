import { createContext, useContext, type ReactNode } from "react";
import { defaultViews, type DesktopViewSettings } from "./bridge";
const Context = createContext<{
  view: DesktopViewSettings;
  update: (patch: Partial<DesktopViewSettings>) => void;
}>({ view: defaultViews(), update: () => {} });
export function DesktopViewProvider({
  view,
  onChange,
  children,
}: {
  view: DesktopViewSettings;
  onChange: (view: DesktopViewSettings) => void;
  children: ReactNode;
}) {
  return (
    <Context.Provider
      value={{ view, update: (patch) => onChange({ ...view, ...patch }) }}
    >
      {children}
    </Context.Provider>
  );
}
export const useDesktopView = () => useContext(Context);
