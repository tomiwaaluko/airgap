import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { buildCsp, cspIsRestrictive } from "@/lib/csp";
import { middleware } from "@/middleware";

const PATHS = [
  "/",
  "/audit",
  "/policies",
  "/api/health",
  "/api/events",
  "/api/pending",
  "/api/audit",
  "/api/policies",
];

describe("content-security-policy", () => {
  it("is default-src self without unsafe-inline, unsafe-eval, or remote script origin", () => {
    const header = buildCsp("testnonce");
    expect(header).toContain("default-src 'self'");
    expect(cspIsRestrictive(header)).toBe(true);
    expect(header).not.toContain("unsafe-inline");
    expect(header).not.toContain("unsafe-eval");
    expect(header).not.toMatch(/script-src[^;]*https?:\/\//);
  });

  it("is present on dashboard responses", () => {
    for (const path of PATHS) {
      const response = middleware(new NextRequest(`http://127.0.0.1:3000${path}`));
      const header = response.headers.get("content-security-policy");
      expect(header, path).toBeTruthy();
      expect(cspIsRestrictive(header ?? ""), path).toBe(true);
    }
  });
});
