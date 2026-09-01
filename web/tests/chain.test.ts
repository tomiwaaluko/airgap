import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AuditTrail } from "@/components/AuditTrail";
import { annotateAuditEvents } from "@/lib/chain";

describe("audit chain status", () => {
  it("marks in-memory events without hashes as unhashed", () => {
    const view = annotateAuditEvents([
      { event: "request_created", request_id: "deadbeef", payload: {} },
    ]);
    expect(view.chain).toBe("unhashed");
    expect(view.events[0]?.chain).toBe("unhashed");
  });

  it("localizes a broken prev_hash link", () => {
    const view = annotateAuditEvents([
      {
        event: "request_created",
        request_id: "deadbeef",
        payload: {},
        prev_hash: "0".repeat(64),
        row_hash: "a".repeat(64),
      },
      {
        event: "resolved",
        request_id: "deadbeef",
        payload: {},
        prev_hash: "b".repeat(64),
        row_hash: "c".repeat(64),
      },
    ]);
    expect(view.chain).toBe("broken");
    expect(view.first_bad_index).toBe(1);
    expect(view.events[1]?.chain).toBe("broken");
  });

  it("renders a broken chain as an unmistakable banner", () => {
    const html = renderToStaticMarkup(
      createElement(AuditTrail, {
        audit: {
          chain: "broken",
          first_bad_index: 1,
          events: [
            {
              event: "resolved",
              request_id: "deadbeef",
              payload: {},
              chain: "broken",
            },
          ],
        },
      }),
    );
    expect(html).toContain("CHAIN BROKEN");
    expect(html).toContain("data-testid=\"chain-broken\"");
    expect(html).toContain("row-broken");
  });
});
