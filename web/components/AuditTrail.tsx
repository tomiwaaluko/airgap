import type { ReactElement } from "react";

import { formatPayload } from "@/lib/text";
import type { AuditView } from "@/lib/types";

export function AuditTrail({ audit }: { audit: AuditView }): ReactElement {
  const broken = audit.chain === "broken";
  const unhashed = audit.chain === "unhashed";

  return (
    <div className="audit-page">
      {broken ? (
        <p className="chain-broken" role="alert" data-testid="chain-broken">
          CHAIN BROKEN
          {audit.first_bad_index !== null
            ? ` AT ROW ${audit.first_bad_index}`
            : ""}
        </p>
      ) : null}
      {unhashed ? (
        <p className="chain-unhashed" role="status" data-testid="chain-unhashed">
          IN-MEMORY AUDIT — HASH CHAIN NOT PRESENT
        </p>
      ) : null}
      {audit.chain === "ok" ? (
        <p className="chain-ok" role="status">
          CHAIN OK
        </p>
      ) : null}
      <table className="audit-table">
        <thead>
          <tr>
            <th>chain</th>
            <th>event</th>
            <th>request_id</th>
            <th>payload</th>
          </tr>
        </thead>
        <tbody>
          {audit.events.length === 0 ? (
            <tr>
              <td colSpan={4}>No events.</td>
            </tr>
          ) : (
            audit.events.map((row, index) => (
              <tr
                key={`${row.event}-${row.request_id ?? "none"}-${index}`}
                className={row.chain === "broken" ? "row-broken" : undefined}
                data-chain={row.chain}
              >
                <td className={row.chain === "broken" ? "broken-cell" : undefined}>
                  {row.chain.toUpperCase()}
                </td>
                <td>{row.event}</td>
                <td>{row.request_id ?? "—"}</td>
                <td>
                  <pre className="untrusted">{formatPayload(row.payload)}</pre>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
