"use client";

import { useState } from "react";

/**
 * "WHAT IS THIS?" — one-line plain-language explanation, expandable.
 * Short answer always visible; deeper detail on demand. Never hides the science.
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
  return (
    <span style={{ display: "inline-block", position: "relative" }}>
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
