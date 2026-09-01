import type { AuditEventView, AuditView, ChainStatus } from "./types";

const GENESIS = "0".repeat(64);

export type RawAuditEvent = {
  event?: unknown;
  request_id?: unknown;
  payload?: unknown;
  seq?: unknown;
  prev_hash?: unknown;
  row_hash?: unknown;
};

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asRequestId(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function annotateAuditEvents(raw: unknown): AuditView {
  const list = Array.isArray(raw) ? raw : [];
  const events: RawAuditEvent[] = list.filter(
    (row): row is RawAuditEvent => row !== null && typeof row === "object",
  );
  const hashed = events.some(
    (row) => typeof row.row_hash === "string" && typeof row.prev_hash === "string",
  );
  if (!hashed) {
    return {
      events: events.map((row) => toView(row, "unhashed")),
      chain: "unhashed",
      first_bad_index: null,
    };
  }

  let previous = GENESIS;
  let firstBad: number | null = null;
  const views: AuditEventView[] = events.map((row, index) => {
    const prevHash = asString(row.prev_hash);
    const rowHash = asString(row.row_hash);
    const linked = prevHash === previous && typeof rowHash === "string" && rowHash.length === 64;
    if (!linked && firstBad === null) {
      firstBad = index;
    }
    if (typeof rowHash === "string") {
      previous = rowHash;
    }
    const chain: ChainStatus = firstBad !== null && index >= firstBad ? "broken" : "ok";
    return toView(row, chain);
  });

  return {
    events: views,
    chain: firstBad === null ? "ok" : "broken",
    first_bad_index: firstBad,
  };
}

function toView(row: RawAuditEvent, chain: ChainStatus): AuditEventView {
  return {
    event: asString(row.event) ?? "",
    request_id: asRequestId(row.request_id),
    payload: row.payload ?? null,
    seq: typeof row.seq === "number" ? row.seq : undefined,
    prev_hash: asString(row.prev_hash),
    row_hash: asString(row.row_hash),
    chain,
  };
}
