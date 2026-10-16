import { useEffect, useRef, useState, type ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  defaultChart,
  validSettings,
  type ChartSettings,
  type Drawing,
  type IndicatorKey,
} from "./chart-model";
import type { PanelSpec } from "./bridge";
import { Dialog } from "./shared-ui";
export type DrawingTool = "select" | Drawing["kind"];
export function ChartEditor({
  spec,
  layout,
  panel,
  instrument,
  children,
}: {
  spec: PanelSpec;
  layout: string;
  panel: string;
  instrument: string;
  children: (props: {
    settings: ChartSettings;
    drawings: Drawing[];
    tool: DrawingTool;
    drawingColor: string;
    drawingWidth: number;
    onDrawings: (drawings: Drawing[]) => void;
    disabled: boolean;
  }) => ReactNode;
}) {
  const [settings, setSettings] = useState(spec.chart ?? defaultChart()),
    [drawings, setDrawings] = useState<Drawing[]>(
      spec.drawings?.[instrument] ?? [],
    ),
    [tool, setTool] = useState<DrawingTool>("select"),
    [color, setColor] = useState("#EA670C"),
    [width, setWidth] = useState(1),
    [configure, setConfigure] = useState(false),
    [clear, setClear] = useState(false),
    [pending, setPending] = useState(false),
    [error, setError] = useState<string | null>(null),
    [undo, setUndo] = useState<Drawing[][]>([]),
    [redo, setRedo] = useState<Drawing[][]>([]);
  const retry = useRef<(() => Promise<void>) | null>(null);
  const source = JSON.stringify({
    settings: spec.chart ?? defaultChart(),
    drawings: spec.drawings?.[instrument] ?? [],
  });
  useEffect(() => {
    const saved = JSON.parse(source);
    setSettings(saved.settings);
    setDrawings(saved.drawings);
  }, [source]);
  const persist = async (operation: () => Promise<void>) => {
    setPending(true);
    setError(null);
    retry.current = operation;
    try {
      await operation();
      retry.current = null;
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  };
  const saveSettings = (next: ChartSettings) => {
    setSettings(next);
    void persist(() =>
      invoke("terminal_chart", {
        mutation: { type: "settings", layout, panel, settings: next },
      }),
    );
  };
  const saveDrawings = (next: Drawing[], record = true) => {
    if (record) {
      setUndo((old) => [...old, drawings].slice(-50));
      setRedo([]);
    }
    setDrawings(next);
    setTool("select");
    void persist(() =>
      invoke("terminal_chart", {
        mutation: {
          type: "drawings",
          layout,
          panel,
          instrument,
          drawings: next,
        },
      }),
    );
  };
  const disabled = pending || !!error;
  return (
    <div className="chart-editor">
      <div className="analysis-toolbar" aria-label="Chart analysis tools">
        <label>
          Style{" "}
          <select
            aria-label="Chart style"
            value={settings.style}
            disabled={disabled}
            onChange={(e) =>
              saveSettings({
                ...settings,
                style: e.target.value as ChartSettings["style"],
              })
            }
          >
            <option value="candles">Candles</option>
            <option value="line">Line</option>
          </select>
        </label>
        <button disabled={disabled} onClick={() => setConfigure(true)}>
          Indicators
        </button>
        <details className="drawing-menu">
          <summary>Draw</summary>
          <div>
            {(
              [
                "select",
                "trend",
                "horizontal",
                "rectangle",
                "fibonacci",
              ] as const
            ).map((value) => (
              <button
                key={value}
                disabled={
                  disabled || (value !== "select" && drawings.length >= 100)
                }
                aria-pressed={tool === value}
                onClick={(e) => {
                  setTool(value);
                  e.currentTarget.closest("details")?.removeAttribute("open");
                }}
              >
                {
                  {
                    select: "Select",
                    trend: "Trend line",
                    horizontal: "Horizontal level",
                    rectangle: "Rectangle",
                    fibonacci: "Fibonacci retracement",
                  }[value]
                }
              </button>
            ))}
            <label>
              Color
              <input
                aria-label="Drawing color"
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
              />
            </label>
            <label>
              Width
              <select
                aria-label="Drawing width"
                value={width}
                onChange={(e) => setWidth(Number(e.target.value))}
              >
                {[1, 2, 3, 4].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
          </div>
        </details>
        <span className="drawing-active">
          {tool === "select" ? "Select / pan" : tool} · {drawings.length}/100
        </span>
        <button
          disabled={disabled || !undo.length}
          onClick={() => {
            const previous = undo[undo.length - 1];
            setUndo(undo.slice(0, -1));
            setRedo((old) => [...old, drawings].slice(-50));
            saveDrawings(previous, false);
          }}
        >
          Undo
        </button>
        <button
          disabled={disabled || !redo.length}
          onClick={() => {
            const next = redo[redo.length - 1];
            setRedo(redo.slice(0, -1));
            setUndo((old) => [...old, drawings].slice(-50));
            saveDrawings(next, false);
          }}
        >
          Redo
        </button>
        <button
          disabled={disabled || !drawings.length}
          onClick={() => setClear(true)}
        >
          Clear drawings
        </button>
        {pending && <span role="status">Saving…</span>}
      </div>
      {error && (
        <div className="chart-save-error" role="alert">
          Unsaved chart draft: {error}
          <button
            disabled={pending}
            onClick={() => {
              const operation = retry.current;
              if (operation) void persist(operation);
            }}
          >
            Retry
          </button>
          <button
            disabled={pending}
            onClick={() => {
              setSettings(spec.chart ?? defaultChart());
              setDrawings(spec.drawings?.[instrument] ?? []);
              setError(null);
              setUndo([]);
              setRedo([]);
              retry.current = null;
            }}
          >
            Discard
          </button>
        </div>
      )}
      {children({
        settings,
        drawings,
        tool,
        drawingColor: color,
        drawingWidth: width,
        onDrawings: (next) => saveDrawings(next),
        disabled,
      })}
      {configure && (
        <IndicatorDialog
          settings={settings}
          onClose={() => setConfigure(false)}
          onSave={(next) => {
            setConfigure(false);
            saveSettings(next);
          }}
        />
      )}
      {clear && (
        <Dialog title="Clear chart drawings" onClose={() => setClear(false)}>
          <p>
            Remove all drawings for this panel and instrument? Undo remains
            available in this session.
          </p>
          <button
            onClick={() => {
              setClear(false);
              saveDrawings([]);
            }}
          >
            Clear all drawings
          </button>
          <button onClick={() => setClear(false)}>Cancel</button>
        </Dialog>
      )}
    </div>
  );
}
function IndicatorDialog({
  settings,
  onSave,
  onClose,
}: {
  settings: ChartSettings;
  onSave: (s: ChartSettings) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(structuredClone(settings));
  const labels: Record<IndicatorKey, string> = {
    sma: "SMA",
    ema: "EMA",
    bollinger: "Bollinger Bands",
    rsi: "RSI",
    macd: "MACD",
    volume: "Volume",
  };
  return (
    <Dialog title="Indicator settings" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (validSettings(draft)) onSave(draft);
        }}
      >
        <p>
          Calculated locally from loaded, unadjusted provider OHLC. Warm-up
          values are unavailable. Bollinger bands use population standard
          deviation ×2.
        </p>
        {(Object.keys(labels) as IndicatorKey[]).map((key) => (
          <fieldset key={key}>
            <legend>{labels[key]}</legend>
            <label>
              <input
                type="checkbox"
                checked={draft[key].enabled}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [key]: { ...draft[key], enabled: e.target.checked },
                  })
                }
              />
              Visible
            </label>
            {draft[key].periods.map((period, i) => (
              <label key={i}>
                {key === "macd"
                  ? ["Fast", "Slow", "Signal"][i]
                  : `Period ${i + 1}`}
                <input
                  aria-label={`${labels[key]} ${key === "macd" ? ["fast", "slow", "signal"][i] : `period ${i + 1}`}`}
                  type="number"
                  min="2"
                  max="250"
                  step="1"
                  required
                  value={period}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      [key]: {
                        ...draft[key],
                        periods: draft[key].periods.map((p, j) =>
                          i === j ? Number(e.target.value) : p,
                        ),
                      },
                    })
                  }
                />
              </label>
            ))}
            <label>
              Color
              <input
                type="color"
                aria-label={`${labels[key]} color`}
                value={draft[key].color}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [key]: { ...draft[key], color: e.target.value },
                  })
                }
              />
            </label>
            <label>
              Width
              <select
                aria-label={`${labels[key]} width`}
                value={draft[key].width}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    [key]: { ...draft[key], width: Number(e.target.value) },
                  })
                }
              >
                {[1, 2, 3, 4].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => setDraft({ ...draft, [key]: defaultChart()[key] })}
            >
              Reset {labels[key]}
            </button>
          </fieldset>
        ))}
        {!validSettings(draft) && (
          <p role="alert">
            Use integer periods 2–250; MACD fast must be less than slow.
          </p>
        )}
        <button disabled={!validSettings(draft)}>Apply indicators</button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </form>
    </Dialog>
  );
}
