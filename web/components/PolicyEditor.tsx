"use client";

import { useState, type ReactElement } from "react";

import { isWidening, WIDEN_WARNING } from "@/lib/policy";
import type { PolicyRow } from "@/lib/types";

const ACTIONS = ["escalate", "block", "auto_approve"] as const;

export function WidenWarning({ visible }: { visible: boolean }): ReactElement | null {
  if (!visible) {
    return null;
  }
  return (
    <p className="widen" role="alert" data-testid="widen-warning">
      {WIDEN_WARNING}
    </p>
  );
}

export function PolicyEditor({ rows }: { rows: PolicyRow[] }): ReactElement {
  const [drafts, setDrafts] = useState<PolicyRow[]>(rows);
  const [createPattern, setCreatePattern] = useState("");
  const [message, setMessage] = useState("");
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});

  async function save(row: PolicyRow, original: PolicyRow | undefined): Promise<void> {
    const widening = isWidening(original?.action ?? "", row.action);
    if (widening && !confirmed[row.tool_pattern]) {
      setMessage("Confirm the widening warning before saving.");
      return;
    }
    setMessage("");
    const response = await fetch("/api/policies", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool_pattern: row.tool_pattern,
        action: row.action,
        min_dial: row.min_dial,
        relay_gated: row.relay_gated,
        dwell_s: row.dwell_s,
        confirm_widen: widening,
      }),
    });
    if (!response.ok) {
      setMessage(`Save failed (${response.status})`);
      return;
    }
    const payload = (await response.json()) as { policies: PolicyRow[] };
    setDrafts(payload.policies);
    setMessage(`Saved ${row.tool_pattern}. updated_by is shown per row.`);
  }

  return (
    <div className="policy-page">
      <p className="note">
        Edits persist <code>updated_by</code> on the policy row. Frozen audit
        vocabulary has no policy-edit event — the editor is the record of who
        wrote the row.
      </p>
      {drafts.map((row) => {
        const original = rows.find((item) => item.tool_pattern === row.tool_pattern);
        const widening = isWidening(original?.action ?? "", row.action);
        return (
          <form
            key={row.tool_pattern}
            className="policy-card"
            onSubmit={(event) => {
              event.preventDefault();
              void save(row, original);
            }}
          >
            <h2>{row.tool_pattern}</h2>
            <p className="who">
              updated_by: {row.updated_by || "—"}
            </p>
            <label>
              action
              <select
                value={row.action}
                onChange={(event) => {
                  const action = event.target.value;
                  setDrafts((current) =>
                    current.map((item) =>
                      item.tool_pattern === row.tool_pattern
                        ? { ...item, action }
                        : item,
                    ),
                  );
                }}
              >
                {ACTIONS.map((action) => (
                  <option key={action} value={action}>
                    {action}
                  </option>
                ))}
              </select>
            </label>
            <label>
              min_dial
              <input
                type="number"
                value={row.min_dial}
                onChange={(event) => {
                  const min_dial = Number(event.target.value);
                  setDrafts((current) =>
                    current.map((item) =>
                      item.tool_pattern === row.tool_pattern
                        ? { ...item, min_dial }
                        : item,
                    ),
                  );
                }}
              />
            </label>
            <label>
              dwell_s
              <input
                type="number"
                value={row.dwell_s}
                onChange={(event) => {
                  const dwell_s = Number(event.target.value);
                  setDrafts((current) =>
                    current.map((item) =>
                      item.tool_pattern === row.tool_pattern
                        ? { ...item, dwell_s }
                        : item,
                    ),
                  );
                }}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={row.relay_gated}
                onChange={(event) => {
                  const relay_gated = event.target.checked;
                  setDrafts((current) =>
                    current.map((item) =>
                      item.tool_pattern === row.tool_pattern
                        ? { ...item, relay_gated }
                        : item,
                    ),
                  );
                }}
              />
              relay_gated
            </label>
            <WidenWarning visible={widening} />
            {widening ? (
              <label className="check">
                <input
                  type="checkbox"
                  checked={Boolean(confirmed[row.tool_pattern])}
                  onChange={(event) => {
                    const checked = event.target.checked;
                    setConfirmed((current) => ({
                      ...current,
                      [row.tool_pattern]: checked,
                    }));
                  }}
                />
                I intend to widen authority
              </label>
            ) : null}
            <button type="submit">Save policy</button>
          </form>
        );
      })}
      <form
        className="policy-card"
        onSubmit={(event) => {
          event.preventDefault();
          if (!createPattern) {
            return;
          }
          const row: PolicyRow = {
            tool_pattern: createPattern,
            min_dial: 10,
            action: "escalate",
            relay_gated: false,
            dwell_s: 60,
            updated_by: "",
          };
          setDrafts((current) =>
            current.some((item) => item.tool_pattern === row.tool_pattern)
              ? current
              : [...current, row],
          );
          setCreatePattern("");
        }}
      >
        <h2>new pattern</h2>
        <label>
          tool_pattern
          <input
            value={createPattern}
            onChange={(event) => setCreatePattern(event.target.value)}
          />
        </label>
        <button type="submit">Add row</button>
      </form>
      {message ? <p className="note">{message}</p> : null}
    </div>
  );
}
