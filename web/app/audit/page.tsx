import type { ReactElement } from "react";

import { Dashboard } from "@/components/Dashboard";
import { LiveAudit } from "@/components/LiveAudit";
import { loadSnapshot } from "@/lib/broker";

export const dynamic = "force-dynamic";

export default async function AuditPage(): Promise<ReactElement> {
  const initial = await loadSnapshot();
  return (
    <Dashboard initial={initial} active="audit">
      <LiveAudit />
    </Dashboard>
  );
}
