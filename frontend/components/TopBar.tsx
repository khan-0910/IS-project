"use client";

import { useEffect, useState } from "react";

export default function TopBar({
  meta,
  status,
}: {
  meta?: { k: string; v: string }[];
  status?: "ONLINE" | "RUNNING" | "IDLE";
}) {
  const [utc, setUtc] = useState("--:--:--");
  useEffect(() => {
    const tick = () =>
      setUtc(
        new Date().toISOString().slice(11, 19) + "Z"
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const st = status ?? "ONLINE";
  return (
    <div className="topbar">
      <a href="/" className="wordmark">
        EXO<span>VET</span>
      </a>
      <div className="sep" />
      <span className="label m">EXOPLANET VETTING INSTRUMENT · TESS</span>
      <div className="topmeta">
        {meta?.map((m) => (
          <span key={m.k}>
            {m.k}&nbsp;&nbsp;<span style={{ color: "var(--text)" }}>{m.v}</span>
          </span>
        ))}
        <span>
          <span
            className={`dot${st === "RUNNING" ? " pulse" : ""}`}
            style={
              st === "IDLE" ? { background: "var(--faint)" } : undefined
            }
          />
          {st}
        </span>
        <span>{utc}</span>
      </div>
    </div>
  );
}
