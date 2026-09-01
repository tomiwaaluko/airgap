import { NextResponse } from "next/server";

import { getAuditEvents } from "@/lib/broker";
import { annotateAuditEvents } from "@/lib/chain";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(): Promise<NextResponse> {
  try {
    const events = await getAuditEvents();
    return NextResponse.json(annotateAuditEvents(events), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "audit failed" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
