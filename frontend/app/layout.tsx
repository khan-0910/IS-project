import type { Metadata } from "next";
import { grotesk, mono } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "EXOVET — Exoplanet Vetting Instrument",
  description:
    "AI-assisted exoplanet vetting on TESS photometry. Detection, classification, uncertainty and physical fits — every number traceable.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${grotesk.variable} ${mono.variable}`}>
      <body>
        <a className="skip-link" href="#content">
          Skip to content
        </a>
        <div id="content">{children}</div>
      </body>
    </html>
  );
}
