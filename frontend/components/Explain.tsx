"use client";

import { useEffect, useRef, useState } from "react";

/**
 * WHY / HOW explanation system.
 *
 * Renders one or two small buttons ([ WHY? ] [ HOW? ], optionally
 * [ TECHNICAL ]) that expand inline panels. Panels never open by default —
 * the explanation is there if the user asks, never forced on them.
 * Clicking anywhere outside closes the panel, Escape closes it too.
 *
 * Used by every stage EXOVET explains in plain language (search, cleaning,
 * period validation, classification, uncertainty, fitting) plus the home and
 * about how-it-works sequences.
 */
export default function Explain({
  why,
  how,
  tech,
  whyLabel = "WHY?",
  howLabel = "HOW?",
  techLabel = "TECHNICAL DETAILS",
  /** render as block (an own row) or inline (flows beside a heading) */
  inline = false,
}: {
  why?: string;
  how?: string;
  /** optional extra content shown under HOW — a table, numbers, provenance */
  tech?: React.ReactNode;
  whyLabel?: string;
  howLabel?: string;
  techLabel?: string;
  inline?: boolean;
}) {
  const [panel, setPanel] = useState<"none" | "why" | "how">("none");
  const [techOpen, setTechOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const open = panel !== "none";

  // close on any outside click / on Escape
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setPanel("none");
        setTechOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("touchstart", onDown, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("touchstart", onDown, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPanel("none");
        setTechOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (!why && !how && !tech) return null;

  const pick = (p: "why" | "how") => setPanel(panel === p ? "none" : p);

  const buttons = (
    <div
      className="explain-btns"
      role="group"
      style={inline ? { display: "inline-flex" } : undefined}
    >
      {why && (
        <button
          type="button"
          className={`explain-btn${panel === "why" ? " open" : ""}`}
          onClick={() => pick("why")}
          aria-expanded={panel === "why"}
        >
          {whyLabel}
        </button>
      )}
      {how && (
        <button
          type="button"
          className={`explain-btn${panel === "how" ? " open" : ""}`}
          onClick={() => pick("how")}
          aria-expanded={panel === "how"}
        >
          {howLabel}
        </button>
      )}
    </div>
  );

  return (
    <div className={`explain${inline ? " inline" : ""}`} ref={rootRef}>
      {buttons}
      {panel === "why" && why && (
        <div className="explain-panel" role="note">
          {why}
        </div>
      )}
      {(panel === "how" && (how || tech)) && (
        <div className="explain-panel" role="note">
          {how}
          {how && tech && (
            <div style={{ height: 14 }} />
          )}
          {tech && (
            <>
              <div style={{ height: 14 }} />
              <button
                type="button"
                className={`explain-btn small${techOpen ? " open" : ""}`}
                onClick={() => setTechOpen(!techOpen)}
                aria-expanded={techOpen}
              >
                {techLabel} {techOpen ? "▾" : "▸"}
              </button>
              {techOpen && <div className="explain-tech">{tech}</div>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
