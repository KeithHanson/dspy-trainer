import { describe, expect, it, vi } from "vitest";
import { createLiveDashboardProvider, mapDashboardOverview } from "./liveDashboardProvider";

describe("liveDashboardProvider", () => {
  it("maps API payloads into dashboard overview shape", () => {
    const overview = mapDashboardOverview({
      plans: [
        {
          id: "plan-1",
          status: "running",
          plan_name: "Plan One",
          module_import_id: "mod-1",
          bundle_path: "bundle-a",
          completed_tasks: 2,
          failed_tasks: 1,
          eval_pass_count: 4,
          eval_fail_count: 2,
          total_tasks: 10,
          average_score: 0.6,
          created_at: new Date().toISOString(),
        },
      ],
      modules: [{ id: "mod-1", bundle_name: "support-triage", bundle_version: "3", validation_status: "passed" }],
      workers: [{ worker_id: "w1", status: "listening" }],
    });

    expect(overview.summaryLine).toContain("1 active jobs");
    expect(overview.kpis).toHaveLength(5);
    expect(overview.kpis[0].label).toBe("Recent pass rate");
    expect(overview.kpis[0].value).toBe("66.7%");
    expect(overview.kpis[0].delta).toBe("4/6");
    expect(overview.kpis[1].label).toBe("Recent average score");
    expect(overview.kpis[1].value).toBe("0.600");
    expect(overview.kpis[2].label).toBe("Pending evals");
    expect(overview.kpis[2].value).toBe("7");
    expect(overview.kpis[4].label).toBe("Workers online");
    expect(overview.kpis[4].value).toBe("1/1");
    expect(overview.liveJob?.id).toBe("plan-1");
    expect(overview.liveJob?.bundleName).toBe("support-triage v3");
    expect(overview.recentJobs[0].planName).toBe("Plan One");
    expect(overview.recentJobs[0].bundleName).toBe("support-triage v3");
  });

  it("fetches plans/modules/workers from API", async () => {
    const fetchMock = vi.fn((url) => {
      const asString = String(url);
      if (asString.includes("/agent-run-plans")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (asString.endsWith("/modules")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      if (asString.endsWith("/workers")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      return Promise.reject(new Error(`unexpected URL ${asString}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const provider = createLiveDashboardProvider("http://localhost:8000");
    const overview = await provider.getOverview();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(overview).toHaveProperty("summaryLine");
    vi.unstubAllGlobals();
  });
});
