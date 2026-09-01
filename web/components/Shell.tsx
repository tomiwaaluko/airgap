"use client";

import Link from "next/link";
import type { ReactElement, ReactNode } from "react";

import { useSnapshot } from "@/components/SnapshotProvider";

export function Shell({
  active,
  children,
}: {
  active: "queue" | "audit" | "policy";
  children: ReactNode;
}): ReactElement {
  const snapshot = useSnapshot();
  const link = snapshot.health.link;
  const pending = snapshot.pending.armed
    ? snapshot.pending.queue.length + 1
    : snapshot.pending.queue.length;

  return (
    <div className="frame">
      <header className="top">
        <div className="brand">
          <p className="kicker">Z2 reader · ui scope · no resolve path</p>
          <h1>AIRGAP</h1>
        </div>
        <dl className="status">
          <div>
            <dt>link</dt>
            <dd className={link === "up" ? "up" : "down"}>{link.toUpperCase()}</dd>
          </div>
          <div>
            <dt>pending</dt>
            <dd>{pending}</dd>
          </div>
          <div>
            <dt>health</dt>
            <dd>{snapshot.health.ok ? "OK" : "FAIL"}</dd>
          </div>
        </dl>
        <nav className="nav" aria-label="Dashboard">
          <Link href="/" className={active === "queue" ? "current" : undefined}>
            Queue
          </Link>
          <Link href="/audit" className={active === "audit" ? "current" : undefined}>
            Audit
          </Link>
          <Link
            href="/policies"
            className={active === "policy" ? "current" : undefined}
          >
            Policy
          </Link>
        </nav>
      </header>
      <p className="banner" role="note">
        Approval happens on the device. This surface cannot resolve a request.
      </p>
      {snapshot.error ? (
        <p className="error" role="alert">
          {snapshot.error}
        </p>
      ) : null}
      <main className="main">{children}</main>
    </div>
  );
}
