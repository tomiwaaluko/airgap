import { NextResponse } from "next/server";

import { getPending } from "@/lib/broker";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(): Promise<NextResponse> {
  try {
    const pending = await getPending();
    return NextResponse.json(pending, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "pending failed" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
