"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import type { DashboardSnapshot } from "@/lib/types";

const SnapshotContext = createContext<DashboardSnapshot | null>(null);

export function SnapshotProvider({
  initial,
  children,
}: {
  initial: DashboardSnapshot;
  children: ReactNode;
}): ReactElement {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>(initial);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = (event: MessageEvent<string>) => {
      try {
        const next = JSON.parse(event.data) as DashboardSnapshot;
        setSnapshot(next);
      } catch {
        return;
      }
    };
    return () => {
      source.close();
    };
  }, []);

  return (
    <SnapshotContext.Provider value={snapshot}>{children}</SnapshotContext.Provider>
  );
}

export function useSnapshot(): DashboardSnapshot {
  const snapshot = useContext(SnapshotContext);
  if (snapshot === null) {
    throw new Error("SnapshotProvider required");
  }
  return snapshot;
}
