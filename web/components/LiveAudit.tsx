"use client";

import type { ReactElement } from "react";

import { AuditTrail } from "@/components/AuditTrail";
import { useSnapshot } from "@/components/SnapshotProvider";

export function LiveAudit(): ReactElement {
  const snapshot = useSnapshot();
  return <AuditTrail audit={snapshot.audit} />;
}
