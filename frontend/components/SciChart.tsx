"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type Pt = [number, number];

export interface SciSeries {
  points: Pt[];
  color: string;
  width?: number;
  dash?: string;
  opacity?: number;
  asPoints?: boolean;
  animate?: boolean;
  /** flat translucent fill down to the chart floor (solid, not gradient) */
  fill?: string;
}

export interface SciMarker {
  x: number;
  label?: string;
  color?: string;
}

export interface SciBand {
  from: number;
  to: number;
  color?: string;
}

interface Props {
  series: SciSeries[];
  height?: number;
  xLabel?: string;
  yLabel?: string;
  logX?: boolean;
  yDomain?: [number, number];
  xDomain?: [number, number];
  markers?: SciMarker[];
  bands?: SciBand[];
  tickX?: number;
  animate?: boolean;
  emptyText?: string;
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return { ref, w };
}

const LOG = (v: number) => Math.log10(Math.max(v, 1e-12));

function niceTicks(a: number, b: number, n = 5): number[] {
  const span = b - a;
  if (span <= 0 || !isFinite(span)) return [a];
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(a / step) * step; v <= b + step * 1e-6; v += step) {
    out.push(Number(v.toPrecision(12)));
  }
  return out;
}

function logTicks(a: number, b: number): number[] {
  const out: number[] = [];
  const start = Math.floor(LOG(a));
  const end = Math.ceil(LOG(b));
  for (let e = start; e <= end; e++) {
    for (const m of [1, 2, 5]) {
      const v = m * Math.pow(10, e);
      if (v >= a * 0.999 && v <= b * 1.001) out.push(v);
    }
  }
  return out;
}

function fmtLog(v: number): string {
  // log ticks are always 1/2/5 x 10^n: print them compactly (1, 2, 5, 10, 0.5)
  const av = Math.abs(v);
  if (av >= 10000 || (av < 0.01 && av > 0)) return v.toExponential(0).replace("e+", "e");
  if (av >= 100) return v.toFixed(0);
  return String(Number(v.toPrecision(6)));
}

function fmt(v: number, step?: number): string {
  const av = Math.abs(v);
  if (av === 0) return "0";
  // small tick steps need more decimals than the magnitude rules give,
  // otherwise adjacent ticks collapse to identical labels (e.g. flux 1.000/1.001)
  if (step != null && step > 0 && step < 1) {
    const dec = Math.min(8, Math.max(3, Math.ceil(-Math.log10(step) + 0.3)) + 1);
    return v.toFixed(dec);
  }
  if (av >= 10000 || av < 0.01) return v.toExponential(0).replace("e+", "e");
  if (av >= 100) return v.toFixed(0);
  if (av >= 10) return v.toFixed(1);
  if (av >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

export default function SciChart({
  series,
  height = 260,
  xLabel,
  yLabel,
  logX = false,
  yDomain,
  xDomain,
  markers = [],
  bands = [],
  tickX = 5,
  animate = true,
  emptyText = "NO DATA",
}: Props) {
  const { ref, w } = useWidth();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const padL = 46,
    padR = 12,
    padT = 14 + (yLabel ? 10 : 0), // caption gets its own row above the top tick
    padB = 26 + (xLabel ? 12 : 0); // caption gets its own row below the ticks
  const tickBaseline = height - 8 - (xLabel ? 12 : 0);
  const iw = Math.max(w - padL - padR, 10);
  const ih = height - padT - padB;

  const xfn = logX ? LOG : (v: number) => v;

  // domains
  let xs: number[] = [];
  let ys: number[] = [];
  for (const s of series) {
    for (const [x, y] of s.points) {
      if (!isFinite(x) || !isFinite(y)) continue;
      xs.push(x);
      ys.push(y);
    }
  }
  for (const m of markers) xs.push(m.x);
  for (const b of bands) {
    xs.push(b.from, b.to);
  }
  if (!xs.length || !ys.length) {
    return (
      <div ref={ref} style={{ height }}>
        <div
          style={{
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid var(--line)",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.2em",
            color: "var(--faint)",
          }}
        >
          {emptyText}
        </div>
      </div>
    );
  }

  let x0 = xDomain ? xDomain[0] : Math.min(...xs);
  let x1 = xDomain ? xDomain[1] : Math.max(...xs);
  if (logX) {
    x0 = xDomain ? LOG(xDomain[0]) : LOG(Math.max(x0, 1e-9));
    x1 = xDomain ? LOG(xDomain[1]) : LOG(x1);
  }
  const span = (x1 - x0) || 1;
  x0 -= span * 0.015;
  x1 += span * 0.015;

  let y0 = yDomain ? yDomain[0] : Math.min(...ys);
  let y1 = yDomain ? yDomain[1] : Math.max(...ys);
  if (!yDomain) {
    const pad = (y1 - y0) * 0.08 || 0.001;
    y0 -= pad;
    y1 += pad;
  }

  const X = (v: number) => padL + ((xfn(v) - x0) / (x1 - x0)) * iw;
  const Y = (v: number) => padT + ih - ((v - y0) / (y1 - y0 || 1)) * ih;

  const xticks = logX
    ? logTicks(Math.pow(10, x0), Math.pow(10, x1))
    : niceTicks(x0, x1, tickX);
  const yticks = niceTicks(y0, y1, 4);
  const stepOf = (t: number[]) => (t.length > 1 ? Math.abs(t[1] - t[0]) : undefined);
  const xstep = logX ? undefined : stepOf(xticks);
  const ystep = stepOf(yticks);

  return (
    <div ref={ref} className="chart-frame" style={{ height }}>
      {w > 0 && (
        <svg width={w} height={height}>
          {/* bands */}
          {bands.map((b, i) => (
            <rect
              key={i}
              x={X(b.from)}
              y={padT}
              width={Math.max(X(b.to) - X(b.from), 1)}
              height={ih}
              fill={b.color || "var(--accent-faint)"}
            />
          ))}

          {/* grid */}
          {yticks.map((t, i) => (
            <g key={`y${i}`}>
              <line
                x1={padL}
                x2={padL + iw}
                y1={Y(t)}
                y2={Y(t)}
                stroke="var(--line)"
                strokeWidth={1}
              />
              <text className="tick" x={padL - 6} y={Y(t) + 3} textAnchor="end">
                {fmt(t, ystep)}
              </text>
            </g>
          ))}
          {xticks.map((t, i) => (
            <g key={`x${i}`}>
              <line
                x1={X(t)}
                x2={X(t)}
                y1={padT}
                y2={padT + ih}
                stroke="var(--line)"
                strokeWidth={1}
                strokeDasharray="2 5"
                opacity={0.55}
              />
              <text className="tick" x={X(t)} y={tickBaseline} textAnchor="middle">
                {logX ? fmtLog(t) : fmt(t, xstep)}
              </text>
            </g>
          ))}

          {/* frame */}
          <rect
            x={padL}
            y={padT}
            width={iw}
            height={ih}
            fill="none"
            stroke="var(--line-strong)"
            strokeWidth={1}
          />

          {/* markers */}
          {markers.map((m, i) => (
            <g key={`m${i}`}>
              <line
                x1={X(m.x)}
                x2={X(m.x)}
                y1={padT}
                y2={padT + ih}
                stroke={m.color || "var(--accent)"}
                strokeWidth={1}
                strokeDasharray="4 3"
              />
              {m.label && (
                <text
                  className="tick"
                  x={X(m.x) + 4}
                  y={padT + 10}
                  fill={m.color || "var(--accent)"}
                >
                  {m.label}
                </text>
              )}
            </g>
          ))}

          {/* series */}
          {series.map((s, i) => {
            if (!s.points.length) return null;
            if (s.asPoints) {
              return (
                <g key={i}>
                  {s.points.map(([x, y], j) => (
                    <circle
                      key={j}
                      cx={X(x)}
                      cy={Y(y)}
                      r={1.2}
                      fill={s.color}
                      opacity={s.opacity ?? 0.85}
                    />
                  ))}
                </g>
              );
            }
            const d = s.points
              .map(
                ([x, y], j) =>
                  `${j === 0 ? "M" : "L"}${X(x).toFixed(2)},${Y(y).toFixed(2)}`
              )
              .join("");
            return (
              <g>
                {s.fill && s.points.length > 1 && (
                  <path
                    d={`${d}L${X(s.points[s.points.length - 1][0]).toFixed(2)},${(
                      padT + ih
                    ).toFixed(2)}L${X(s.points[0][0]).toFixed(2)},${(
                      padT + ih
                    ).toFixed(2)}Z`}
                    fill={s.fill}
                    stroke="none"
                  />
                )}
                <path
                  key={i}
                  d={d}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={s.width ?? 1.2}
                  strokeDasharray={s.dash}
                  opacity={s.opacity ?? 1}
                  pathLength={s.animate && animate && mounted ? 1 : undefined}
                  className={
                    s.animate && animate && mounted ? "draw" : undefined
                  }
                />
              </g>
            );
          })}

          {/* axis captions */}
          {xLabel && (
            <text
              className="axis-label"
              x={padL + iw}
              y={height - 8}
              textAnchor="end"
            >
              {xLabel}
            </text>
          )}
          {yLabel && (
            <text className="axis-label" x={4} y={9}>
              {yLabel}
            </text>
          )}
        </svg>
      )}
    </div>
  );
}
