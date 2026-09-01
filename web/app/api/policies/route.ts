import { NextResponse } from "next/server";

import { getPolicies, putPolicy } from "@/lib/broker";
import { isWidening } from "@/lib/policy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(): Promise<NextResponse> {
  try {
    const policies = await getPolicies();
    return NextResponse.json(
      { policies },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "policies failed" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}

type PutBody = {
  tool_pattern?: unknown;
  action?: unknown;
  min_dial?: unknown;
  relay_gated?: unknown;
  dwell_s?: unknown;
  confirm_widen?: unknown;
};

export async function PUT(request: Request): Promise<NextResponse> {
  let body: PutBody;
  try {
    body = (await request.json()) as PutBody;
  } catch {
    return NextResponse.json({ error: "json required" }, { status: 400 });
  }
  if (typeof body.tool_pattern !== "string" || body.tool_pattern.length === 0) {
    return NextResponse.json({ error: "tool_pattern required" }, { status: 400 });
  }
  if (typeof body.action !== "string") {
    return NextResponse.json({ error: "action required" }, { status: 400 });
  }
  if (typeof body.min_dial !== "number") {
    return NextResponse.json({ error: "min_dial required" }, { status: 400 });
  }
  const current = await getPolicies().catch(() => []);
  const existing = current.find((row) => row.tool_pattern === body.tool_pattern);
  if (isWidening(existing?.action ?? "", body.action) && body.confirm_widen !== true) {
    return NextResponse.json(
      { error: "confirm_widen required to set auto_approve" },
      { status: 400 },
    );
  }
  try {
    const policies = await putPolicy(body.tool_pattern, {
      action: body.action,
      min_dial: body.min_dial,
      relay_gated: body.relay_gated === true,
      dwell_s: typeof body.dwell_s === "number" ? body.dwell_s : 60,
    });
    return NextResponse.json(
      { policies },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "policies PUT failed" },
      { status: 502 },
    );
  }
}
