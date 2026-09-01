import { NextResponse } from "next/server";

import { getHealth } from "@/lib/broker";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(): Promise<NextResponse> {
  try {
    const health = await getHealth();
    return NextResponse.json(health, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return NextResponse.json(
      { ok: false, link: "down", pending: 0, error: String(error) },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
