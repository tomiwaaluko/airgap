import type { ReactElement } from "react";

import { Dashboard } from "@/components/Dashboard";
import { LiveQueue } from "@/components/LiveQueue";
import { loadSnapshot } from "@/lib/broker";

export const dynamic = "force-dynamic";

export default async function QueuePage(): Promise<ReactElement> {
  const initial = await loadSnapshot();
  return (
    <Dashboard initial={initial} active="queue">
      <LiveQueue />
    </Dashboard>
  );
}
