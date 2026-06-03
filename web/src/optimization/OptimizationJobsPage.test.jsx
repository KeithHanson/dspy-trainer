import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
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
            source_run_plan_id: "plan-111",
            generated_module_import_id: "mod-2",
            optimized_evaluation_plan_id: "eval-plan-1",
            optimized_eval_run_plan_id: "run-plan-1",
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
            execution_log: "job=opt-job-001\nstatus=running\nstatus=succeeded",
          }),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }, { id: "mod-2", bundle_name: "Echo optimized", bundle_version: "0.4.0" }]) });
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
    expect(await screen.findByText("opt-job-001")).toBeInTheDocument();
    expect(await screen.findByText("Strategy")).toBeInTheDocument();
    expect(await screen.findByText("gepa")).toBeInTheDocument();
    expect(await screen.findByText("Baseline score")).toBeInTheDocument();
    expect(await screen.findByText("50.0%")).toBeInTheDocument();
    expect(await screen.findByText("Optimized score")).toBeInTheDocument();
    expect(await screen.findByText("100.0%")).toBeInTheDocument();
    expect(await screen.findByText("Delta")).toBeInTheDocument();
    expect(await screen.findByText("+50.0%")).toBeInTheDocument();
    expect(await screen.findByText("Generated outputs")).toBeInTheDocument();
    expect(await screen.findByText("Echo optimized")).toBeInTheDocument();
    expect(await screen.findByText("eval-plan-1")).toBeInTheDocument();
    expect(await screen.findByText("run-plan-1")).toBeInTheDocument();
    expect(await screen.findByText("Process log")).toBeInTheDocument();
    expect(await screen.findByText(/job=opt-job-001/i)).toBeInTheDocument();

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

  it("shows backend failure reason on failed optimization job detail", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/optimization/jobs/opt-job-failed") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "opt-job-failed",
            status: "failed",
            module_import_id: "mod-1",
            strategy: "bootstrap_fewshot",
            objective: "optimize_demo_quality",
            execution_lm_profile_id: "lm-1",
            helper_lm_profile_id: null,
            dataset_id: null,
            validation_dataset_id: null,
            source_run_plan_id: "plan-111",
            created_at: "2026-01-01T00:00:00+00:00",
            run_started_at: "2026-01-01T00:01:00+00:00",
            finished_at: "2026-01-01T00:05:00+00:00",
            artifact_path: null,
            artifact_metadata: {},
            comparison_summary: {},
            telemetry_summary: {},
            request_config: {},
            normalized_config: {},
            execution_log: "job=opt-job-failed\nstatus=running\nstatus=failed\nerror=optimization dataset produced no usable demo examples",
            failure_reason: "optimization dataset produced no usable demo examples",
          }),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "Execution Profile" }]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/optimization/jobs?job=opt-job-failed"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Optimization job failed")).toBeInTheDocument();
    expect(await screen.findByText("optimization dataset produced no usable demo examples")).toBeInTheDocument();
    expect(await screen.findByText(/status=failed/i)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("updates the process log while a job is still running", async () => {
    let requestCount = 0;
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/optimization/jobs/opt-job-live") && init?.method === "GET") {
        requestCount += 1;
        const payload = requestCount === 1
          ? {
              id: "opt-job-live",
              status: "running",
              module_import_id: "mod-1",
              strategy: "bootstrap_fewshot",
              objective: "optimize_demo_quality",
              execution_lm_profile_id: "lm-1",
              helper_lm_profile_id: null,
              dataset_id: null,
              validation_dataset_id: null,
              source_run_plan_id: "plan-111",
              created_at: "2026-01-01T00:00:00+00:00",
              run_started_at: "2026-01-01T00:01:00+00:00",
              finished_at: null,
              artifact_path: null,
              artifact_metadata: {},
              comparison_summary: {},
              telemetry_summary: {},
              request_config: {},
              normalized_config: {},
              execution_log: "job=opt-job-live\nstatus=running",
              failure_reason: null,
            }
          : {
              id: "opt-job-live",
              status: "running",
              module_import_id: "mod-1",
              strategy: "bootstrap_fewshot",
              objective: "optimize_demo_quality",
              execution_lm_profile_id: "lm-1",
              helper_lm_profile_id: null,
              dataset_id: null,
              validation_dataset_id: null,
              source_run_plan_id: "plan-111",
              created_at: "2026-01-01T00:00:00+00:00",
              run_started_at: "2026-01-01T00:01:00+00:00",
              finished_at: null,
              artifact_path: null,
              artifact_metadata: {},
              comparison_summary: {},
              telemetry_summary: {},
              request_config: {},
              normalized_config: {},
              execution_log: "job=opt-job-live\nstatus=running\nphase=optimized_eval:start",
              failure_reason: null,
            };
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue(payload) });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "Execution Profile" }]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/optimization/jobs?job=opt-job-live"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/job=opt-job-live/i)).toBeInTheDocument();
    expect(await screen.findByText("Live run output refreshes automatically.")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/phase=optimized_eval:start/i)).toBeInTheDocument();
    }, { timeout: 4000 });

    vi.unstubAllGlobals();
  });

  it("deletes an optimization job from the list", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/optimization/jobs?limit=50&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "opt-job-delete",
              strategy: "bootstrap_fewshot",
              objective: "optimize_demo_quality",
              status: "queued",
              module_import_id: "mod-1",
              comparison_summary: {},
              run_started_at: "2026-01-01T00:01:00+00:00",
            },
          ]),
        });
      }
      if (String(url).endsWith("/optimization/jobs/opt-job-delete") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "opt-job-delete", deleted: true }) });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(
      <MemoryRouter initialEntries={["/optimization/jobs"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("opt-job-de")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.queryByText("opt-job-de")).not.toBeInTheDocument();
    });

    vi.unstubAllGlobals();
  });

  it("cancels an optimization job from the list", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/optimization/jobs?limit=50&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "opt-job-cancel",
              strategy: "bootstrap_fewshot",
              objective: "optimize_demo_quality",
              status: "running",
              module_import_id: "mod-1",
              comparison_summary: {},
              run_started_at: "2026-01-01T00:01:00+00:00",
            },
          ]),
        });
      }
      if (String(url).endsWith("/optimization/jobs/opt-job-cancel/cancel") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "opt-job-cancel",
            strategy: "bootstrap_fewshot",
            objective: "optimize_demo_quality",
            status: "canceled",
            module_import_id: "mod-1",
            comparison_summary: {},
            run_started_at: "2026-01-01T00:01:00+00:00",
          }),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(
      <MemoryRouter initialEntries={["/optimization/jobs"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("opt-job-ca")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.getByText("canceled")).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("cancels an optimization job from the detail page", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/optimization/jobs/opt-job-live") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "opt-job-live",
            status: "running",
            module_import_id: "mod-1",
            strategy: "bootstrap_fewshot",
            objective: "optimize_demo_quality",
            execution_lm_profile_id: "lm-1",
            helper_lm_profile_id: null,
            dataset_id: null,
            validation_dataset_id: null,
            source_run_plan_id: "plan-111",
            created_at: "2026-01-01T00:00:00+00:00",
            run_started_at: "2026-01-01T00:01:00+00:00",
            finished_at: null,
            artifact_path: null,
            artifact_metadata: {},
            comparison_summary: {},
            telemetry_summary: {},
            request_config: {},
            normalized_config: {},
            execution_log: "job=opt-job-live\nstatus=running",
            failure_reason: null,
          }),
        });
      }
      if (String(url).endsWith("/optimization/jobs/opt-job-live/cancel") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "opt-job-live",
            status: "canceled",
            module_import_id: "mod-1",
            strategy: "bootstrap_fewshot",
            objective: "optimize_demo_quality",
            execution_lm_profile_id: "lm-1",
            helper_lm_profile_id: null,
            dataset_id: null,
            validation_dataset_id: null,
            source_run_plan_id: "plan-111",
            created_at: "2026-01-01T00:00:00+00:00",
            run_started_at: "2026-01-01T00:01:00+00:00",
            finished_at: null,
            artifact_path: null,
            artifact_metadata: {},
            comparison_summary: {},
            telemetry_summary: {},
            request_config: {},
            normalized_config: {},
            execution_log: "job=opt-job-live\nstatus=running\nstatus=canceled",
            failure_reason: null,
          }),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "Execution Profile" }]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(
      <MemoryRouter initialEntries={["/optimization/jobs?job=opt-job-live"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Optimization Job Detail")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Cancel job" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Cancel job" }));

    await waitFor(() => {
      expect(screen.getByText("canceled")).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();
    expect(screen.queryByText("Live run output refreshes automatically.")).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("shows pending generated outputs while optimization is still running", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/optimization/jobs/opt-job-001") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "opt-job-001",
            status: "running",
            module_import_id: "mod-1",
            strategy: "gepa",
            objective: "optimize_judge_feedback",
            execution_lm_profile_id: "lm-1",
            helper_lm_profile_id: null,
            dataset_id: "ods-1",
            validation_dataset_id: null,
            source_run_plan_id: "plan-111",
            generated_module_import_id: null,
            optimized_evaluation_plan_id: null,
            optimized_eval_run_plan_id: null,
            created_at: "2026-01-01T00:00:00+00:00",
            run_started_at: "2026-01-01T00:01:00+00:00",
            finished_at: "2026-01-01T00:05:00+00:00",
            artifact_path: "programs/opt-job-001/program.json",
            artifact_metadata: { artifact_type: "dspy_program_state" },
            comparison_summary: { baseline_score_pct: 50, optimized_score_pct: 100, score_delta_pct: 50 },
            telemetry_summary: {},
            request_config: {},
            normalized_config: {},
            execution_log: "job=opt-job-001\nstatus=succeeded",
            failure_reason: null,
          }),
        });
      }
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "mod-1", bundle_name: "Echo", bundle_version: "0.3.0" }]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "Execution Profile" }]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/optimization/jobs?job=opt-job-001"]}>
        <OptimizationJobsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Generated outputs")).toBeInTheDocument();
    const pendingValues = screen.getAllByText("Pending");
    expect(pendingValues.length).toBeGreaterThanOrEqual(3);

    vi.unstubAllGlobals();
  });
});
