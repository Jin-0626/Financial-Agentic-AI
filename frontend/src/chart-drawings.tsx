import type { PointerEvent } from "react";
import { anchorToPoint, type Drawing } from "./chart-model";
export function DrawingLayer({
  drawings,
  times,
  left,
  right,
  step,
  y,
  selected,
  onSelect,
  onHandle,
  interactive,
}: {
  drawings: Drawing[];
  times: number[];
  left: number;
  right: number;
  step: number;
  y: (price: number) => number;
  selected: string | null;
  onSelect: (id: string) => void;
  onHandle: (
    event: PointerEvent<SVGCircleElement>,
    drawing: Drawing,
    index: number,
  ) => void;
  interactive: boolean;
}) {
  return (
    <g className={`drawing-layer ${interactive ? "" : "inactive"}`}>
      {drawings.map((d) => {
        const [x1, y1] = anchorToPoint(d.anchors[0], times, left, step, y),
          [x2, y2] = anchorToPoint(d.anchors[1], times, left, step, y);
        return (
          <g
            key={d.id}
            data-drawing-id={d.id}
            stroke={d.color}
            strokeWidth={d.width}
            fill="none"
            onPointerDown={(e) => {
              e.stopPropagation();
              onSelect(d.id);
              e.currentTarget.ownerSVGElement?.focus();
            }}
          >
            <title>
              {d.kind} drawing · select and drag handles; Delete removes
            </title>
            {d.kind === "trend" ? (
              <line x1={x1} y1={y1} x2={x2} y2={y2} />
            ) : d.kind === "horizontal" ? (
              <line x1={left} y1={y1} x2={right} y2={y1} />
            ) : d.kind === "rectangle" ? (
              <rect
                x={Math.min(x1, x2)}
                y={Math.min(y1, y2)}
                width={Math.max(1, Math.abs(x2 - x1))}
                height={Math.max(1, Math.abs(y2 - y1))}
              />
            ) : (
              <g>
                {[0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map((level) => (
                  <g key={level}>
                    <line
                      x1={Math.min(x1, x2)}
                      x2={Math.max(x1, x2)}
                      y1={y1 + (y2 - y1) * level}
                      y2={y1 + (y2 - y1) * level}
                    />
                    <text
                      x={Math.min(x1, x2) + 3}
                      y={y1 + (y2 - y1) * level - 3}
                      stroke="none"
                      fill={d.color}
                      fontSize="10"
                    >
                      {(level * 100).toFixed(1)}%
                    </text>
                  </g>
                ))}
              </g>
            )}
            {selected === d.id &&
              d.anchors
                .slice(0, d.kind === "horizontal" ? 1 : 2)
                .map((a, i) => {
                  const [x, at] = anchorToPoint(a, times, left, step, y);
                  return (
                    <circle
                      key={i}
                      aria-label={`Edit ${d.kind} anchor ${i + 1}`}
                      cx={x}
                      cy={at}
                      r="5"
                      fill="#13161C"
                      strokeWidth="2"
                      onPointerDown={(e) => onHandle(e, d, i)}
                    />
                  );
                })}
          </g>
        );
      })}
    </g>
  );
}
