import { describe, expect, it, vi } from "vitest";
import { buildAbsoluteApiUrl, buildApiUrl, buildMlflowUrl, normalizeApiBaseUrl, normalizeMlflowBaseUrl } from "./base";

describe("api base helpers", () => {
  it("defaults browser-facing API calls to the proxy-relative /api base", () => {
    expect(normalizeApiBaseUrl()).toBe("/api");
    expect(buildApiUrl("/modules")).toBe("/api/modules");
  });

  it("preserves explicitly configured absolute API bases", () => {
    expect(normalizeApiBaseUrl("http://localhost:8000/")).toBe("http://localhost:8000");
    expect(buildApiUrl("/modules", "https://example.test/api/")).toBe("https://example.test/api/modules");
    expect(buildAbsoluteApiUrl("/modules", "https://example.test/api/")).toBe("https://example.test/api/modules");
  });

  it("builds absolute browser URLs from proxy-relative API defaults", () => {
    vi.stubGlobal("window", { location: { origin: "http://localhost:8082" } });
    expect(buildAbsoluteApiUrl("/modules")).toBe("http://localhost:8082/api/modules");
    vi.unstubAllGlobals();
  });

  it("defaults MLflow links to the proxy-relative /mlflow base", () => {
    expect(normalizeMlflowBaseUrl()).toBe("/mlflow");
    expect(buildMlflowUrl("/#/experiments/1")).toBe("/mlflow/#/experiments/1");
  });
});
