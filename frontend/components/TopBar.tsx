"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "HOME" },
  { href: "/analyze", label: "ANALYZE" },
  { href: "/results", label: "RESULTS" },
  { href: "/verify", label: "VERIFY" },
  { href: "/about", label: "ABOUT" },
];

/** Minimal top bar: wordmark, nav, status. All instrument trivia removed. */
export default function TopBar({
  meta,
  status,
}: {
  meta?: { k: string; v: string }[];
  status?: "ONLINE" | "RUNNING" | "IDLE";
}) {
  const pathname = usePathname() ?? "/";
  const st = status ?? "ONLINE";

  return (
    <div className="topbar">
      <Link href="/" className="wordmark">
        EXO<span>VET</span>
      </Link>
      <div className="sep" />
      <nav className="mainnav" aria-label="Primary">
        {NAV.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            className={`navlink${pathname === n.href ? " active" : ""}`}
            aria-current={pathname === n.href ? "page" : undefined}
          >
            {n.label}
          </Link>
        ))}
      </nav>
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
      </div>
    </div>
  );
}
