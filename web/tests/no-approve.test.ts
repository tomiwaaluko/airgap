import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Dashboard } from "@/components/Dashboard";
import { LiveQueue } from "@/components/LiveQueue";
import { PolicyEditor } from "@/components/PolicyEditor";

import { EMPTY_SNAPSHOT } from "./fixtures";

const WEB_ROOT = join(__dirname, "..");
const SCAN_ROOTS = ["app", "components", "lib", "middleware.ts", "next.config.ts"];

const FORBIDDEN = [
  /\/decide\b/,
  /['"`]\/approve(?:\/|"|'|`|\?)/i,
  /['"`]\/deny(?:\/|"|'|`|\?)/i,
  /request_approval/,
  /dangerouslySetInnerHTML/,
  /\.innerHTML\s*=/,
];

function walk(target: string): string[] {
  const files: string[] = [];
  const info = statSync(target);
  if (info.isFile()) {
    return [target];
  }
  for (const entry of readdirSync(target)) {
    if (entry === "node_modules" || entry === ".next") {
      continue;
    }
    files.push(...walk(join(target, entry)));
  }
  return files;
}

describe("no dashboard resolve path", () => {
  it("has no decide/approve/deny route, fetch, or HTML injection sink", () => {
    const files = SCAN_ROOTS.flatMap((root) => walk(join(WEB_ROOT, root)));
    const hits: string[] = [];
    for (const file of files) {
      if (!/\.(ts|tsx|js|mjs)$/.test(file)) {
        continue;
      }
      const text = readFileSync(file, "utf8");
      const rel = relative(WEB_ROOT, file);
      for (const pattern of FORBIDDEN) {
        if (pattern.test(text)) {
          hits.push(`${rel} matches ${pattern}`);
        }
      }
    }
    expect(hits).toEqual([]);
  });

  it("renders no approve, deny, or never control", () => {
    const html = [
      renderToStaticMarkup(
        createElement(
          Dashboard,
          {
            initial: EMPTY_SNAPSHOT,
            active: "queue",
          },
          createElement(LiveQueue),
        ),
      ),
      renderToStaticMarkup(
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
      ),
    ].join("\n");
    expect(html).not.toMatch(/<button[^>]*>\s*(Approve|Deny|Never)\s*</i);
    expect(html).not.toMatch(/href="[^"]*\/(approve|deny|decide)/i);
    expect(html).toContain("cannot resolve a request");
  });
});
