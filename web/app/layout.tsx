import type { Metadata } from "next";
import type { ReactElement, ReactNode } from "react";

import "./globals.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Airgap",
  description: "Live consent queue. No software approve path.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>): ReactElement {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
