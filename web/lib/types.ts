export type LinkStatus = "up" | "down";

export type HealthPayload = {
  ok: boolean;
  link: LinkStatus;
  pending: number;
};

export type PendingItem = {
  request_id: string;
  actor: string;
  tool_name: string;
  tool_args: unknown;
  justification: string;
  risk_class: string;
  short_code: string | null;
  relay_gated: boolean;
  reasoning: string;
  dial: number;
  elapsed_s: number;
  policy: Record<string, unknown> | null;
};

export type PendingPayload = {
  link: LinkStatus;
  armed: PendingItem | null;
  queue: PendingItem[];
};

export type PolicyRow = {
  tool_pattern: string;
  min_dial: number;
  action: string;
  relay_gated: boolean;
  dwell_s: number;
  updated_by: string;
};

export type PolicyUpdate = {
  action: string;
  min_dial: number;
  relay_gated: boolean;
  dwell_s: number;
};

export type ChainStatus = "ok" | "broken" | "unhashed";

export type AuditEventView = {
  event: string;
  request_id: string | null;
  payload: unknown;
  seq?: number;
  prev_hash?: string;
  row_hash?: string;
  chain: ChainStatus;
};

export type AuditView = {
  events: AuditEventView[];
  chain: ChainStatus;
  first_bad_index: number | null;
};

export type DashboardSnapshot = {
  health: HealthPayload;
  pending: PendingPayload;
  audit: AuditView;
  at: number;
  error?: string;
};
