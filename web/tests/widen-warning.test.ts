import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PolicyEditor, WidenWarning } from "@/components/PolicyEditor";
import { isWidening, WIDEN_WARNING } from "@/lib/policy";

describe("policy widen warning", () => {
  it("detects anything to auto_approve as widening", () => {
    expect(isWidening("escalate", "auto_approve")).toBe(true);
    expect(isWidening("block", "auto_approve")).toBe(true);
    expect(isWidening("", "auto_approve")).toBe(true);
    expect(isWidening("auto_approve", "auto_approve")).toBe(false);
    expect(isWidening("auto_approve", "block")).toBe(false);
  });

  it("shows the N-T8 warning before submit when widening", () => {
    const html = renderToStaticMarkup(createElement(WidenWarning, { visible: true }));
    expect(html).toContain("data-testid=\"widen-warning\"");
    expect(html).toContain(WIDEN_WARNING);
    expect(html).toContain("WIDENS AUTHORITY");
  });

  it("hides the warning when the draft is not a widen", () => {
    const html = renderToStaticMarkup(
      createElement(PolicyEditor, {
        rows: [
          {
            tool_pattern: "db.drop_*",
            min_dial: 10,
            action: "escalate",
            relay_gated: false,
            dwell_s: 60,
            updated_by: "ui",
          },
        ],
      }),
    );
    expect(html).not.toContain("WIDENS AUTHORITY");
    expect(html).toContain("updated_by: ui");
  });
});
