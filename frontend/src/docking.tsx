import { useRef, useState, type ReactNode } from "react";
import type { DockNode } from "./bridge";

type Edge = "center" | "left" | "right" | "top" | "bottom";
function mapTree(
  node: DockNode,
  id: string,
  transform: (node: DockNode) => DockNode,
): DockNode {
  if (node.id === id) return transform(node);
  return node.type === "tabs"
    ? node
    : {
        ...node,
        first: mapTree(node.first, id, transform),
        second: mapTree(node.second, id, transform),
      };
}
export function removePanel(node: DockNode, panel: string): DockNode | null {
  if (node.type === "tabs") {
    const panels = node.panels.filter((item) => item !== panel);
    return panels.length
      ? {
          ...node,
          panels,
          active: panels.includes(node.active) ? node.active : panels[0],
        }
      : null;
  }
  const first = removePanel(node.first, panel),
    second = removePanel(node.second, panel);
  return first && second ? { ...node, first, second } : (first ?? second);
}
export function movePanel(
  root: DockNode,
  panel: string,
  target: string,
  edge: Edge,
): DockNode {
  // Retain the source when moving its sole tab onto its own group.
  const find = (node: DockNode): DockNode | null =>
    node.id === target
      ? node
      : node.type === "split"
        ? (find(node.first) ?? find(node.second))
        : null;
  const destination = find(root);
  if (
    !destination ||
    destination.type !== "tabs" ||
    (destination.panels.includes(panel) &&
      (edge === "center" || destination.panels.length === 1))
  )
    return root;
  const cleaned = removePanel(root, panel);
  if (!cleaned) return root;
  return mapTree(cleaned, target, (node) => {
    if (node.type !== "tabs") return node;
    if (edge === "center")
      return { ...node, panels: [...node.panels, panel], active: panel };
    const fresh: DockNode = {
      type: "tabs",
      id: crypto.randomUUID(),
      panels: [panel],
      active: panel,
    };
    const before = edge === "left" || edge === "top";
    return {
      type: "split",
      id: crypto.randomUUID(),
      axis: edge === "left" || edge === "right" ? "horizontal" : "vertical",
      ratio: 0.5,
      first: before ? fresh : node,
      second: before ? node : fresh,
    };
  });
}
export function DockWorkspace({
  root,
  onChange,
  panels,
  titles = {},
}: {
  root: DockNode;
  onChange: (root: DockNode) => void;
  panels: Record<string, ReactNode>;
  titles?: Record<string, string>;
}) {
  const [drag, setDrag] = useState<string | null>(null);
  const [maximized, setMaximized] = useState<string | null>(null);
  const [target, setTarget] = useState<string>("");
  const [edge, setEdge] = useState<Edge>("center");
  const groups: string[] = [];
  const collect = (node: DockNode) => {
    if (node.type === "tabs") groups.push(node.id);
    else {
      collect(node.first);
      collect(node.second);
    }
  };
  collect(root);
  function render(node: DockNode): ReactNode {
    if (node.type === "split")
      return (
        <Split
          key={node.id}
          node={node}
          onRatio={(ratio) =>
            onChange(
              mapTree(root, node.id, (n) =>
                n.type === "split" ? { ...n, ratio } : n,
              ),
            )
          }
        >
          {render(node.first)}
          {render(node.second)}
        </Split>
      );
    return (
      <section
        className="dock-group"
        key={node.id}
        hidden={!!maximized && !node.panels.includes(maximized)}
      >
        <div className="dock-tabs" role="tablist" aria-label="Docked panels">
          {node.panels.map((panel) => (
            <button
              key={panel}
              role="tab"
              aria-selected={panel === node.active}
              draggable
              onDragStart={() => setDrag(panel)}
              onDragEnd={() => setDrag(null)}
              onDragOver={(event) => {
                if (drag) event.preventDefault();
              }}
              onDrop={(event) => {
                if (!drag || drag === panel) return;
                event.preventDefault();
                event.stopPropagation();
                const moved = node.panels.includes(drag)
                  ? root
                  : movePanel(root, drag, node.id, "center");
                onChange(
                  mapTree(moved, node.id, (group) => {
                    if (group.type !== "tabs") return group;
                    const panels = group.panels.filter((id) => id !== drag);
                    panels.splice(panels.indexOf(panel), 0, drag);
                    return { ...group, panels, active: drag };
                  }),
                );
                setDrag(null);
              }}
              onClick={() =>
                onChange(
                  mapTree(root, node.id, (n) =>
                    n.type === "tabs" ? { ...n, active: panel } : n,
                  ),
                )
              }
            >
              {(titles[panel] ?? panel).toUpperCase()}
            </button>
          ))}
          <button
            aria-label={`Maximize ${node.active}`}
            onClick={() => setMaximized(maximized ? null : node.active)}
          >
            {maximized ? "Restore" : "□"}
          </button>
          <button
            aria-label={`Close ${node.active}`}
            disabled={groups.length === 1 && node.panels.length === 1}
            onClick={() => {
              const next = removePanel(root, node.active);
              if (next) {
                setMaximized(null);
                onChange(next);
              }
            }}
          >
            ×
          </button>
          {node.panels.length > 1 && (
            <button
              aria-label={`Move ${node.active} tab left`}
              onClick={() =>
                onChange(
                  mapTree(root, node.id, (n) => {
                    if (n.type !== "tabs") return n;
                    const panels = [...n.panels],
                      index = panels.indexOf(n.active);
                    if (index > 0)
                      [panels[index - 1], panels[index]] = [
                        panels[index],
                        panels[index - 1],
                      ];
                    return { ...n, panels };
                  }),
                )
              }
            >
              ←
            </button>
          )}
          <details className="dock-menu">
            <summary aria-label={`Move ${node.active}`}>Move</summary>
            <div>
              <select
                aria-label="Destination group"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">Choose group</option>
                {groups
                  .filter((id) => id !== node.id)
                  .map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
              </select>
              <select
                aria-label="Dock position"
                value={edge}
                onChange={(e) => setEdge(e.target.value as Edge)}
              >
                {["center", "left", "right", "top", "bottom"].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
              <button
                disabled={!target}
                onClick={() => {
                  onChange(movePanel(root, node.active, target, edge));
                  setTarget("");
                }}
              >
                Apply
              </button>
            </div>
          </details>
        </div>
        <div className="dock-content">
          {panels[node.active] ?? (
            <div className="empty">Panel unavailable</div>
          )}
        </div>
        {drag && (
          <div className="dock-targets">
            {(["left", "right", "top", "bottom", "center"] as Edge[]).map(
              (position) => (
                <div
                  key={position}
                  className={`dock-drop ${position}`}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    onChange(movePanel(root, drag, node.id, position));
                    setDrag(null);
                  }}
                >
                  {position}
                </div>
              ),
            )}
          </div>
        )}
      </section>
    );
  }
  // Render only the chosen group while maximized; keep the persisted tree unchanged.
  const findMax = (node: DockNode): DockNode | null =>
    node.type === "tabs"
      ? node.panels.includes(maximized ?? "")
        ? node
        : null
      : (findMax(node.first) ?? findMax(node.second));
  return (
    <div className="dock-workspace">
      {render(maximized ? (findMax(root) ?? root) : root)}
    </div>
  );
}
function Split({
  node,
  onRatio,
  children,
}: {
  node: Extract<DockNode, { type: "split" }>;
  onRatio: (ratio: number) => void;
  children: ReactNode[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const ratio = useRef(node.ratio);
  const [preview, setPreview] = useState<number | null>(null);
  const horizontal = node.axis === "horizontal";
  const value = preview ?? node.ratio;
  return (
    <div
      ref={ref}
      className={`dock-split ${node.axis}`}
      style={{
        [horizontal ? "gridTemplateColumns" : "gridTemplateRows"]:
          `minmax(${horizontal ? 210 : 250}px, ${value}fr) 7px minmax(${horizontal ? 210 : 250}px, ${1 - value}fr)`,
      }}
    >
      {children[0]}
      <div
        role="separator"
        tabIndex={0}
        aria-label="Resize panels"
        aria-orientation={horizontal ? "vertical" : "horizontal"}
        aria-valuemin={15}
        aria-valuemax={85}
        aria-valuenow={Math.round(value * 100)}
        onKeyDown={(e) => {
          if (
            !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)
          )
            return;
          e.preventDefault();
          e.stopPropagation();
          onRatio(
            Math.max(
              0.15,
              Math.min(
                0.85,
                value +
                  (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -0.05 : 0.05),
              ),
            ),
          );
        }}
        onPointerDown={(e) => {
          ratio.current = node.ratio;
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!e.currentTarget.hasPointerCapture(e.pointerId) || !ref.current)
            return;
          const rect = ref.current.getBoundingClientRect();
          ratio.current = Math.max(
            0.15,
            Math.min(
              0.85,
              horizontal
                ? (e.clientX - rect.left) / rect.width
                : (e.clientY - rect.top) / rect.height,
            ),
          );
          setPreview(ratio.current);
        }}
        onPointerUp={(e) => {
          e.currentTarget.releasePointerCapture(e.pointerId);
          onRatio(ratio.current);
          setPreview(null);
        }}
        onPointerCancel={() => setPreview(null)}
        className="dock-divider"
      />
      {children[1]}
    </div>
  );
}
