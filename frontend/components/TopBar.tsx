"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Home", tip: "Project overview, live light curve and system status." },
  { href: "/analyze", label: "Analyze", tip: "Run the six-stage pipeline on one TESS star or your own light curve." },
  { href: "/results", label: "Results", tip: "Every stored analysis — opens instantly from disk, no rerun." },
  { href: "/verify", label: "Verify", tip: "Score the model against ExoFOP ground-truth labels in batch." },
  { href: "/about", label: "About", tip: "How the pipeline works, the five classes, and current limitations." },
];

/** Sticky top bar: wordmark, primary nav, optional run metadata + status. */
export default function TopBar({
  meta,
  status,
}: {
  meta?: { k: string; v: string }[];
  status?: "ONLINE" | "RUNNING" | "IDLE" | "OFFLINE";
}) {
  const pathname = usePathname() ?? "/";
  const st = status ?? "ONLINE";
  const dot = st === "RUNNING" ? "run" : st === "IDLE" ? "idle" : st === "OFFLINE" ? "off" : "";

  return (
    <header className="topbar">
      <Link href="/" className="wordmark" aria-label="EXOVET home">
        <span className="orb" aria-hidden />
        EXO<b>VET</b>
      </Link>
      <nav className="mainnav" aria-label="Primary">
        {NAV.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            className={`navlink tip-below${pathname === n.href ? " active" : ""}`}
            aria-current={pathname === n.href ? "page" : undefined}
            data-tip={n.tip}
          >
            {n.label}
          </Link>
        ))}
      </nav>
      <div className="topmeta">
        {meta?.map((m) => (
          <span key={m.k} className="kv">
            {m.k} <b>{m.v}</b>
          </span>
        ))}
        <span>
          <span className={`dot ${dot}`} />
          {st}
        </span>
      </div>
    </header>
  );
}
