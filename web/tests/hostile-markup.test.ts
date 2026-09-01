import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArmedPanel } from "@/components/ArmedPanel";
import { Dashboard } from "@/components/Dashboard";
import { LiveQueue } from "@/components/LiveQueue";

import { HOSTILE_ITEM, HOSTILE_SNAPSHOT } from "./fixtures";

describe("hostile markup renders as text", () => {
  it("does not create script or markup elements from tool_args or justification", () => {
    const html = renderToStaticMarkup(createElement(ArmedPanel, { item: HOSTILE_ITEM }));
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>users</b>");
    expect(html).not.toContain("<b>bold</b>");
    expect(html).not.toContain("<em>escalate</em>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("&lt;b&gt;bold&lt;/b&gt;");
    expect(html).toContain("&lt;b&gt;users&lt;/b&gt;");
  });

  it("keeps hostile fields as text on the live queue", () => {
    const html = renderToStaticMarkup(
      createElement(
        Dashboard,
        {
          initial: HOSTILE_SNAPSHOT,
          active: "queue",
        },
        createElement(LiveQueue),
      ),
    );
    expect(html).not.toContain("<script>alert(1)</script>");
    expect(html).toContain("&lt;script&gt;");
  });
});
