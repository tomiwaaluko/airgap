import type { ReactElement } from "react";

import { Dashboard } from "@/components/Dashboard";
import { PolicyEditor } from "@/components/PolicyEditor";
import { getPolicies, loadSnapshot } from "@/lib/broker";
import type { PolicyRow } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PoliciesPage(): Promise<ReactElement> {
  const [initial, rows] = await Promise.all([loadSnapshot(), settlePolicies()]);
  return (
    <Dashboard initial={initial} active="policy">
      <PolicyEditor rows={rows} />
    </Dashboard>
  );
}

async function settlePolicies(): Promise<PolicyRow[]> {
  try {
    return await getPolicies();
  } catch {
    return [];
  }
}
