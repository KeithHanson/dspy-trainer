import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { OptimizationLaunchPage } from "./OptimizationLaunchPage";

describe("OptimizationLaunchPage", () => {
  it("submits scaffolded optimization payload with strategy defaults and run plan preview", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "Echo Support", validation_status: "passed", source_ref: "/tmp/checkouts/echo-support", bundle_version: "0.3.0", current_commit_sha: "abc12345", sync_status: "synced" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "lm-exec-1", name: "Execution Profile" },
            { id: "lm-help-2", name: "Helper Profile" },
          ]),
        });
      }
      if (String(url).endsWith("/optimization/datasets") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "ods-1", name: "Demo corpus", dataset_kind: "demo" },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-1/agent-run-plans?limit=100&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "plan-123",
              plan_name: "Baseline run plan",
              status: "finished",
              created_at: "2026-01-01T00:00:00Z",
            },
            {
              id: "plan-124",
              plan_name: "Retry run plan",
              status: "running",
              created_at: "2026-01-01T00:00:00Z",
            },
          ]),
        });
      }
      if (String(url).endsWith("/agent-run-plans/plan-123/tasks?limit=500&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [
              { id: "task-1", eval_pass: true },
              { id: "task-2", eval_pass: true },
              { id: "task-3", eval_pass: false },
            ],
          }),
        });
      }
      if (String(url).endsWith("/optimization/jobs") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ id: "opt-1" }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <OptimizationLaunchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText("Target module")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "MIPROv2" })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: /Baseline run plan/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "MIPROv2" }));
    await userEvent.selectOptions(screen.getByLabelText("Execution LM profile"), "lm-exec-1");
    await userEvent.selectOptions(screen.getByLabelText("Helper LM profile (optional)"), "lm-help-2");
    await userEvent.selectOptions(screen.getByLabelText("Source run plan"), "plan-123");
    await userEvent.clear(screen.getByLabelText("Target bundle version"));
    await userEvent.type(screen.getByLabelText("Target bundle version"), "2.0.0-preview");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/agent-run-plans\/plan-123\/tasks\?limit=500&offset=0$/),
      expect.objectContaining({ method: "GET" }),
    ));

    const expectedCountLabel = await screen.findByText("Expected records:");
    expect(expectedCountLabel.closest("p")).toHaveTextContent("Expected records: 2");
    expect(await screen.findByText("score_below_threshold: 1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Launch optimization job" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/optimization\/jobs$/),
      expect.objectContaining({ method: "POST" }),
    ));

    const postCall = fetchMock.mock.calls.find(([callUrl, callInit]) =>
      String(callUrl).endsWith("/optimization/jobs") && callInit?.method === "POST",
    );
    expect(postCall).toBeTruthy();
    const payload = JSON.parse(postCall[1].body);

    expect(payload.strategy).toBe("miprov2");
    expect(payload.objective).toBe("optimize_demo_quality");
    expect(payload.module_import_id).toBe("mod-1");
    expect(payload.execution_lm_profile_id).toBe("lm-exec-1");
    expect(payload.helper_lm_profile_id).toBe("lm-help-2");
    expect(payload.dataset_id).toBeNull();
  expect(payload.source_run_plan_id).toBe("plan-123");
    expect(payload.request_config).toMatchObject({
      budget: "light",
      max_bootstrapped_demos: 4,
      max_labeled_demos: 16,
      target_bundle_version: "2.0.0-preview",
    });
    expect(payload.normalized_config).toMatchObject({ optimizer_family: "miprov2", target_bundle_version: "2.0.0-preview" });

    expect(await screen.findByText(/Optimization job queued/)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("scopes source run plans to the selected module and handles no matches", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "Echo Support", validation_status: "passed", source_ref: "/tmp/checkouts/echo-support", bundle_version: "0.3.0", current_commit_sha: "abc12345", sync_status: "synced" },
            { id: "mod-2", bundle_name: "Other Module", validation_status: "passed", source_ref: "/tmp/checkouts/other-module", bundle_version: "0.4.0", current_commit_sha: "def67890", sync_status: "synced" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-exec-1", name: "Execution Profile" }]) });
      }
      if (String(url).endsWith("/optimization/datasets") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules/mod-1/agent-run-plans?limit=100&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-201", plan_name: "Run Plan One", status: "finished", created_at: "2026-01-01T00:00:00Z" },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-2/agent-run-plans?limit=100&offset=0") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <OptimizationLaunchPage />
      </MemoryRouter>,
    );

    const moduleSelect = await screen.findByLabelText("Target module");
    expect(moduleSelect).toBeInTheDocument();
    await userEvent.selectOptions(moduleSelect, "mod-2");

    const sourceRunPlanSelect = await screen.findByLabelText("Source run plan");
    expect(sourceRunPlanSelect).toBeEnabled();
    expect(await screen.findByRole("option", { name: "No matching run plans for this module" })).toBeInTheDocument();

    expect(sourceRunPlanSelect).toHaveValue("");

    vi.unstubAllGlobals();
  });

  it("accepts paginated source run plan payloads", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "Echo Support", validation_status: "passed", source_ref: "/tmp/checkouts/echo-support", bundle_version: "0.3.0", current_commit_sha: "abc12345", sync_status: "synced" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-exec-1", name: "Execution Profile" }]) });
      }
      if (String(url).endsWith("/optimization/datasets") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules/mod-1/agent-run-plans?limit=100&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [
              { id: "plan-777", plan_name: "Paginated run plan", status: "finished", created_at: "2026-01-01T00:00:00Z" },
            ],
            limit: 100,
            offset: 0,
            total: 1,
            count: 1,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <OptimizationLaunchPage />
      </MemoryRouter>,
    );

      const sourceRunPlanSelect = await screen.findByLabelText("Source run plan");
      expect(await screen.findByRole("option", { name: /Paginated run plan/ })).toBeInTheDocument();
      expect(sourceRunPlanSelect).toHaveValue("");

    vi.unstubAllGlobals();
  });

  it("keeps the source run plan selector disabled until a module is selected", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/optimization/datasets") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <OptimizationLaunchPage />
      </MemoryRouter>,
    );

    const sourceRunPlanSelect = await screen.findByLabelText("Source run plan");
    expect(sourceRunPlanSelect).toBeDisabled();
    expect(screen.getByRole("option", { name: "Select a validated module first" })).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("requires an execution LM profile before submit", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "Echo Support", validation_status: "passed", source_ref: "/tmp/checkouts/echo-support", bundle_version: "0.3.0", current_commit_sha: "abc12345", sync_status: "synced" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/optimization/datasets") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <OptimizationLaunchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Launch optimization job" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Launch optimization job" }));

    expect(await screen.findByText("Execution LM profile is required to launch optimization.")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(([callUrl, callInit]) =>
      String(callUrl).endsWith("/optimization/jobs") && callInit?.method === "POST",
    );
    expect(postCall).toBeUndefined();

    vi.unstubAllGlobals();
  });

  it("blocks launch when selected module is behind upstream", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "Echo Support", validation_status: "passed", source_ref: "/tmp/checkouts/echo-support", bundle_version: "0.3.0", current_commit_sha: "abc12345", sync_status: "behind" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-exec-1", name: "Execution Profile" }]) });
      }
      if (String(url).endsWith("/modules/mod-1/agent-run-plans?limit=100&offset=0") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-123", plan_name: "Baseline run plan", status: "finished", created_at: "2026-01-01T00:00:00Z" },
          ]),
        });
      }
      if (String(url).endsWith("/agent-run-plans/plan-123/tasks?limit=500&offset=0") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [] }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <OptimizationLaunchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/Sync status:/)).toBeInTheDocument();
    expect(await screen.findByText(/This bundle cannot be mutated until it is manually synced/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Launch optimization job" })).toBeDisabled();

    vi.unstubAllGlobals();
  });
});
