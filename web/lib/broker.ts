import "server-only";

import { annotateAuditEvents } from "./chain";
import {
  parseAuditEvents,
  parseHealth,
  parsePending,
  parsePolicies,
} from "./parse";
import type {
  DashboardSnapshot,
  HealthPayload,
  PendingPayload,
  PolicyRow,
  PolicyUpdate,
} from "./types";

const DEFAULT_BROKER = "http://127.0.0.1:8741";

function brokerOrigin(): string {
  return process.env.AIRGAP_BROKER_URL ?? DEFAULT_BROKER;
}

function uiToken(): string {
  return process.env.AIRGAP_UI_TOKEN ?? "";
}

function authHeaders(): HeadersInit {
  const token = uiToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function brokerFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${brokerOrigin()}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...authHeaders(),
      ...(init.headers ?? {}),
    },
  });
}

export async function getHealth(): Promise<HealthPayload> {
  const response = await brokerFetch("/health");
  if (!response.ok) {
    throw new Error(`health ${response.status}`);
  }
  return parseHealth(await response.json());
}

export async function getPending(): Promise<PendingPayload> {
  const response = await brokerFetch("/pending");
  if (!response.ok) {
    throw new Error(`pending ${response.status}`);
  }
  return parsePending(await response.json());
}

export async function getPolicies(): Promise<PolicyRow[]> {
  const response = await brokerFetch("/policies");
  if (!response.ok) {
    throw new Error(`policies ${response.status}`);
  }
  return parsePolicies(await response.json());
}

export async function getAuditEvents(): Promise<unknown[]> {
  const response = await brokerFetch("/audit");
  if (!response.ok) {
    throw new Error(`audit ${response.status}`);
  }
  return parseAuditEvents(await response.json());
}

async function csrfToken(): Promise<string> {
  const fromEnv = process.env.AIRGAP_CSRF_SECRET;
  if (fromEnv) {
    return fromEnv;
  }
  const response = await brokerFetch("/pending");
  const cookie = response.headers.get("set-cookie") ?? "";
  const match = /airgap_csrf=([^;]+)/i.exec(cookie);
  if (match === null) {
    throw new Error("csrf cookie missing");
  }
  return decodeURIComponent(match[1]);
}

export async function putPolicy(
  pattern: string,
  update: PolicyUpdate,
): Promise<PolicyRow[]> {
  const csrf = await csrfToken();
  const response = await brokerFetch(`/policies/${encodeURIComponent(pattern)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "x-csrf-token": csrf,
    },
    body: JSON.stringify(update),
  });
  if (!response.ok) {
    throw new Error(`policies PUT ${response.status}`);
  }
  return parsePolicies(await response.json());
}

export async function loadSnapshot(): Promise<DashboardSnapshot> {
  const at = Date.now();
  const healthResult = await settle(getHealth());
  const pendingResult = await settle(getPending());
  const auditResult = await settle(getAuditEvents());

  const errors: string[] = [];
  if (healthResult.error) {
    errors.push("health unreachable");
  }
  if (pendingResult.error) {
    errors.push("pending unreachable");
  }
  if (auditResult.error) {
    errors.push("audit unreachable");
  }

  return {
    health: healthResult.value ?? { ok: false, link: "down", pending: 0 },
    pending: pendingResult.value ?? { link: "down", armed: null, queue: [] },
    audit: annotateAuditEvents(auditResult.value ?? []),
    at,
    error: errors.length > 0 ? errors.join("; ") : undefined,
  };
}

async function settle<T>(
  promise: Promise<T>,
): Promise<{ value?: T; error?: unknown }> {
  try {
    return { value: await promise };
  } catch (error) {
    return { error };
  }
}
