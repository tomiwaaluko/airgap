import type {
  HealthPayload,
  PendingItem,
  PendingPayload,
  PolicyRow,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBool(value: unknown): boolean {
  return value === true;
}

function asLink(value: unknown): "up" | "down" {
  return value === "up" ? "up" : "down";
}

export function parseHealth(value: unknown): HealthPayload {
  if (!isRecord(value)) {
    return { ok: false, link: "down", pending: 0 };
  }
  return {
    ok: value.ok === true,
    link: asLink(value.link),
    pending: asNumber(value.pending),
  };
}

export function parsePendingItem(value: unknown): PendingItem | null {
  if (!isRecord(value)) {
    return null;
  }
  const requestId = asString(value.request_id);
  if (!requestId) {
    return null;
  }
  return {
    request_id: requestId,
    actor: asString(value.actor),
    tool_name: asString(value.tool_name),
    tool_args: value.tool_args ?? {},
    justification: asString(value.justification),
    risk_class: asString(value.risk_class),
    short_code: typeof value.short_code === "string" ? value.short_code : null,
    relay_gated: asBool(value.relay_gated),
    reasoning: asString(value.reasoning),
    dial: asNumber(value.dial),
    elapsed_s: asNumber(value.elapsed_s),
    policy: isRecord(value.policy) ? value.policy : null,
  };
}

export function parsePending(value: unknown): PendingPayload {
  if (!isRecord(value)) {
    return { link: "down", armed: null, queue: [] };
  }
  const queueRaw = Array.isArray(value.queue) ? value.queue : [];
  return {
    link: asLink(value.link),
    armed: parsePendingItem(value.armed),
    queue: queueRaw
      .map((row) => parsePendingItem(row))
      .filter((row): row is PendingItem => row !== null),
  };
}

export function parsePolicies(value: unknown): PolicyRow[] {
  if (!isRecord(value) || !Array.isArray(value.policies)) {
    return [];
  }
  return value.policies.flatMap((row) => {
    if (!isRecord(row)) {
      return [];
    }
    const toolPattern = asString(row.tool_pattern);
    if (!toolPattern) {
      return [];
    }
    return [
      {
        tool_pattern: toolPattern,
        min_dial: asNumber(row.min_dial),
        action: asString(row.action),
        relay_gated: asBool(row.relay_gated),
        dwell_s: asNumber(row.dwell_s, 60),
        updated_by: asString(row.updated_by),
      },
    ];
  });
}

export function parseAuditEvents(value: unknown): unknown[] {
  if (!isRecord(value) || !Array.isArray(value.events)) {
    return [];
  }
  return value.events;
}
