/** @vitest-environment jsdom */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PolicyEditor } from "@/components/PolicyEditor";
import { isWidening, WIDEN_WARNING } from "@/lib/policy";
import type { PolicyRow } from "@/lib/types";

const ESCALATE_ROW: PolicyRow = {
  tool_pattern: "db.drop_*",
  min_dial: 10,
  action: "escalate",
  relay_gated: false,
  dwell_s: 60,
  updated_by: "ui",
};

describe("policy widen warning", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("detects anything to auto_approve as widening", () => {
    expect(isWidening("escalate", "auto_approve")).toBe(true);
    expect(isWidening("block", "auto_approve")).toBe(true);
    expect(isWidening("", "auto_approve")).toBe(true);
    expect(isWidening("auto_approve", "auto_approve")).toBe(false);
    expect(isWidening("auto_approve", "block")).toBe(false);
  });

  it("shows the N-T8 warning when the action select moves to auto_approve, before submit", async () => {
    const user = userEvent.setup();
    render(createElement(PolicyEditor, { rows: [ESCALATE_ROW] }));

    expect(screen.queryByTestId("widen-warning")).toBeNull();
    await user.selectOptions(screen.getByLabelText("action"), "auto_approve");

    const warning = screen.getByTestId("widen-warning");
    expect(warning.textContent).toContain(WIDEN_WARNING);
    expect(warning.textContent).toContain("WIDENS AUTHORITY");
    expect(screen.getByRole("button", { name: "Save policy" })).toBeTruthy();
  });

  it("drops the widen warning after a successful save of auto_approve", async () => {
    const user = userEvent.setup();
    const saved: PolicyRow = {
      ...ESCALATE_ROW,
      action: "auto_approve",
      updated_by: "ui",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ policies: [saved] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(createElement(PolicyEditor, { rows: [ESCALATE_ROW] }));
    await user.selectOptions(screen.getByLabelText("action"), "auto_approve");
    expect(screen.getByTestId("widen-warning")).toBeTruthy();
    await user.click(screen.getByLabelText("I intend to widen authority"));
    await user.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => {
      expect(screen.queryByTestId("widen-warning")).toBeNull();
    });
  });
});
