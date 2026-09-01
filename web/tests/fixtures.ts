import type { DashboardSnapshot, PendingItem } from "@/lib/types";

export const HOSTILE_ITEM: PendingItem = {
  request_id: "deadbeef",
  actor: 'agent/<img src="x" onerror="alert(1)">',
  tool_name: "db.drop_table",
  tool_args: {
    sql: "<script>alert(1)</script>",
    table: "<b>users</b>",
  },
  justification: 'drop it <script>alert(1)</script> and <b>bold</b>',
  risk_class: "high",
  short_code: "K7PQ",
  relay_gated: false,
  reasoning: "warden said <em>escalate</em>",
  dial: 3,
  elapsed_s: 41,
  policy: {
    tool_pattern: "db.drop_*",
    action: "escalate",
  },
};

export const EMPTY_SNAPSHOT: DashboardSnapshot = {
  health: { ok: true, link: "up", pending: 0 },
  pending: { link: "up", armed: null, queue: [] },
  audit: { events: [], chain: "unhashed", first_bad_index: null },
  at: 0,
};

export const HOSTILE_SNAPSHOT: DashboardSnapshot = {
  health: { ok: true, link: "up", pending: 1 },
  pending: { link: "up", armed: HOSTILE_ITEM, queue: [] },
  audit: { events: [], chain: "unhashed", first_bad_index: null },
  at: 0,
};
