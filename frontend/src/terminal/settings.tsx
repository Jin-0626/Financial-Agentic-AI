import {
  Component,
  createContext,
  useContext,
  useState,
  ReactNode,
  Dispatch,
  SetStateAction,
} from "react";
import { Widget, useTerminal } from "./state";
const Context = createContext<Widget | null>(null);
export function WidgetSettings({
  widget,
  children,
}: {
  widget: Widget;
  children: ReactNode;
}) {
  return <Context.Provider value={widget}>{children}</Context.Provider>;
}
export function useWidgetState<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const widget = useContext(Context),
    terminal = useTerminal();
  const [fallback, setFallback] = useState(initial);
  function decode(raw?: string): T {
    try {
      const value = raw ? JSON.parse(raw) : initial;
      if (
        typeof value !== typeof initial ||
        (Array.isArray(initial) &&
          (!Array.isArray(value) ||
            value.some((v: unknown) => typeof v !== "string"))) ||
        (typeof value === "number" && !Number.isFinite(value))
      )
        return initial;
      return value;
    } catch {
      return initial;
    }
  }
  const value = widget ? decode(widget.settings?.[key]) : fallback;
  const set: Dispatch<SetStateAction<T>> = (next) => {
    if (!widget) {
      setFallback(next);
      return;
    }
    terminal.update((old) => ({
      ...old,
      widgets: old.widgets.map((w) => {
        if (w.id !== widget.id) return w;
        const current = decode(w.settings?.[key]);
        const resolved =
          typeof next === "function" ? (next as (v: T) => T)(current) : next;
        return {
          ...w,
          settings: { ...w.settings, [key]: JSON.stringify(resolved) },
        };
      }),
    }));
  };
  return [value, set];
}
export class WidgetBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div role="alert">
        <p>
          This panel could not display its data. Other panels remain available.
        </p>
        <button onClick={() => this.setState({ failed: false })}>
          Retry panel
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
