"use client";

import { useEffect, useRef, useState } from "react";

/**
 * "WHAT IS THIS?" — one-line plain-language explanation, expandable.
 * Short answer always visible; deeper detail on demand. Never hides the science.
 * Clicking anywhere outside closes the note; Escape does too.
 */
export default function Help({
  term,
  text,
  more,
}: {
  term: string;
  text: string;
  more?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
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
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <span ref={rootRef} style={{ display: "inline-block", position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-label={`What is ${term}?`}
        aria-expanded={open}
        className="help-chip"
      >
        {term} ?
      </button>
      {open && (
        <span role="note" className="help-note">
          <b>{term.toUpperCase()}</b> — {text}
          {more && (
            <>
              <br />
              <span style={{ color: "var(--muted)" }}>{more}</span>
            </>
          )}
        </span>
      )}
    </span>
  );
}
