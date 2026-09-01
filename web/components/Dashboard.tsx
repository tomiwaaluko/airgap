"use client";

import type { ReactElement, ReactNode } from "react";

import { Shell } from "@/components/Shell";
import { SnapshotProvider } from "@/components/SnapshotProvider";
import type { DashboardSnapshot } from "@/lib/types";

export function Dashboard({
  initial,
  active,
  children,
}: {
  initial: DashboardSnapshot;
  active: "queue" | "audit" | "policy";
  children: ReactNode;
}): ReactElement {
  return (
    <SnapshotProvider initial={initial}>
      <Shell active={active}>{children}</Shell>
    </SnapshotProvider>
  );
}
