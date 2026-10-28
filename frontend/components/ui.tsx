"use client";

import type { FriendlyError } from "../lib/types";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* one pastel per astrophysical scenario — used everywhere a class appears */
export const CLASS_COLOR: Record<string, string> = {
  planet_candidate: "var(--c-pc)",
  eclipsing_binary: "var(--c-eb)",
  grazing_eclipsing_binary: "var(--c-geb)",
  contamination_blend: "var(--c-blend)",
  instrumental_systematic: "var(--c-sys)",
};

export const CLASS_LABEL: Record<string, string> = {
  planet_candidate: "Planet candidate",
  eclipsing_binary: "Eclipsing binary",
  grazing_eclipsing_binary: "Grazing eclipsing binary",
  contamination_blend: "Contamination / blend",
  instrumental_systematic: "Instrumental / systematic",
};

export const CLASS_SHORT: Record<string, string> = {
  planet_candidate: "PC",
  eclipsing_binary: "EB",
  grazing_eclipsing_binary: "GEB",
  contamination_blend: "BLEND",
  instrumental_systematic: "SYS",
};

export function ClassPill({ c, short = false }: { c?: string | null; short?: boolean }) {
  if (!c) return <span className="pill">—</span>;
  return (
    <span className="pill" style={{ color: CLASS_COLOR[c], borderColor: CLASS_COLOR[c] }}>
      <span className="swatch" style={{ background: CLASS_COLOR[c] }} />
      {short ? CLASS_SHORT[c] ?? c : CLASS_LABEL[c] ?? c}
    </span>
  );
}

/* verdict flags from MC-Dropout — never renamed: ambiguous stays ambiguous */
export function VerdictPill({ verdict }: { verdict?: string | null }) {
  if (!verdict) return <span className="pill">—</span>;
  const v = verdict.toLowerCase();
  const cls = v === "confident" ? "ok" : v === "unstable" ? "bad" : "warn";
  return <span className={`pill ${cls}`}>{v.toUpperCase()}</span>;
}

export function Stat({
  k,
  v,
  s,
  color,
}: {
  k: string;
  v: React.ReactNode;
  s?: React.ReactNode;
  color?: string;
}) {
  return (
    <div className="stat">
      <div className="label">{k}</div>
      <div className="v" style={color ? { color } : undefined}>{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}

export function ErrorBox({ err }: { err: FriendlyError }) {
  return (
    <div className="errbox" role="alert">
      <b>{err.message}</b>
      {err.detail && <div className="row">Why: {err.detail}</div>}
      <div className="row">What to do: {err.action}</div>
    </div>
  );
}

/**
 * "What does each button do?" — a collapsible list of every control on the
 * page with a one-line description. Each control also carries the same text
 * as a hover / keyboard-focus tooltip (data-tip).
 */
export function PageGuide({ items }: { items: [string, string][] }) {
  return (
    <details className="guide">
      <summary>
        <span className="chev">▸</span> What does each button on this page do?
      </summary>
      <div className="guide-list">
        {items.map(([k, d]) => (
          <div className="guide-item" key={k}>
            <span className="k">{k}</span>
            <span>{d}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

export function fmtPeriod(d: number): string {
  if (d >= 2) return `${d.toFixed(2)} days`;
  return `${(d * 24).toFixed(1)} hours`;
}

/** "TIC 55401933.01" / "55401933.01" / "TOI-style planet suffix" -> 55401933.
 *  The ".01" is the planet number on that star; the pipeline analyzes the
 *  whole star, so only the integer TIC id is sent. null if no id is found. */
export function ticFromText(s: string): number | null {
  const m = s.trim().replace(/^TIC[\s-]*/i, "").match(/^(\d+)(?:\.\d+)?$/);
  if (!m) return null;
  const n = Number(m[1]);
  return n > 0 ? n : null;
}

export function fmtNum(v: number | null | undefined, d = 2, unit = ""): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(d) + (unit ? ` ${unit}` : "");
}
