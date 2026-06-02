import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { OptimizationJobsPage } from "./OptimizationJobsPage";

describe("OptimizationJobsPage", () => {
  it("renders optimization job list and navigation intent", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/optimization/jobs?limit=50&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "opt-job-001",
              strategy: "miprov2",
              objective: "optimize_demo_quality",
              status: "succeeded",
              module_import_id: "mod-1",
              comparison_summary: {
                baseline_score_pct: 42,
                optimized_score_pct: 84,
                score_delta_pct: 42,
              },
              run_started_at: "2026-01-01T00:01:00+00:00",
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/optimization/jobs"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Optimization Jobs")).toBeInTheDocument();
    expect(await screen.findByText("miprov2")).toBeInTheDocument();
    expect(await screen.findByText("opt-job-00")).toBeInTheDocument();

    const row = await screen.findByRole("row", { name: /opt-job-00/ });
    expect(row).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("renders optimization job detail with artifact and strategy detail", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/optimization/jobs/opt-job-001") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "opt-job-001",
            status: "succeeded",
            module_import_id: "mod-1",
            strategy: "gepa",
            objective: "optimize_judge_feedback",
            execution_lm_profile_id: "lm-1",
            helper_lm_profile_id: null,
            dataset_id: "ods-1",
            validation_dataset_id: null,
            source_eval_job_id: "plan-111",
            created_at: "2026-01-01T00:00:00+00:00",
            run_started_at: "2026-01-01T00:01:00+00:00",
            finished_at: "2026-01-01T00:05:00+00:00",
            artifact_path: "programs/opt-job-001/program.json",
            artifact_metadata: {
              artifact_type: "dspy_program_state",
              artifact_dir: "/tmp/dspy_trainer/optimization_artifacts/opt-job-001",
            },
            comparison_summary: {
              baseline_score_pct: 50,
              optimized_score_pct: 100,
              score_delta_pct: 50,
            },
            telemetry_summary: {
              strategy: "gepa",
              strategy_details: {
                strategy: "GEPA",
                candidate_count: 3,
              },
            },
            request_config: {
              budget: "medium",
            },
            normalized_config: {
              optimizer_class: "GEPA",
            },
          }),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "lm-1", name: "Execution Profile" },
          ]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/optimization/jobs?job=opt-job-001"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Optimization Job Detail")).toBeInTheDocument();
    expect(await screen.findByText("Job ID: opt-job-001")).toBeInTheDocument();
    expect(await screen.findByText((content) => content.includes("Strategy:") && content.includes("gepa"))).toBeInTheDocument();
    expect(await screen.findByText("dspy_program_state")).toBeInTheDocument();
    expect(await screen.findByText(/programs\/opt-job-001\/program\.json/i)).toBeInTheDocument();
    expect(await screen.findByText("50.0% / 100.0% / +50.0%")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("shows an empty-state message when no jobs exist", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/optimization/jobs?limit=50&offset=0") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/optimization/jobs"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No optimization jobs yet")).toBeInTheDocument();
    expect(await screen.findByText("Launch an optimization job first to see entries here.")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("shows error state when detail fetch fails", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/optimization/jobs/bad-id") && init?.method === "GET") {
        return Promise.resolve({
          ok: false,
          status: 500,
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/optimization/jobs?job=bad-id"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Could not load optimization job")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
