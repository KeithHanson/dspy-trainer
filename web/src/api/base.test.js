import { describe, expect, it } from "vitest";
import { buildApiUrl, normalizeApiBaseUrl } from "./base";

describe("api base helpers", () => {
  it("defaults browser-facing API calls to the proxy-relative /api base", () => {
    expect(normalizeApiBaseUrl()).toBe("/api");
    expect(buildApiUrl("/modules")).toBe("/api/modules");
  });

  it("preserves explicitly configured absolute API bases", () => {
    expect(normalizeApiBaseUrl("http://localhost:8000/")).toBe("http://localhost:8000");
    expect(buildApiUrl("/modules", "https://example.test/api/")).toBe("https://example.test/api/modules");
  });
});
