import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { RunsPage } from "./RunsPage";

describe("RunsPage", () => {
  it("renders jobs list when no plan query", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-1", status: "running", completed_tasks: 1, total_tasks: 6, failed_tasks: 0, created_at: "2026-01-01T00:00:00+00:00" },
          ]),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [{ worker_id: "worker-a", status: "listening", task_id: null, last_seen: "2026-01-01T00:00:00+00:00" }],
            total_workers: 8,
            reported_workers: 1,
            available_workers: 1,
            busy_workers: 0,
          }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Runs")).toBeInTheDocument();
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("shows delete for terminal runs in the jobs list", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-1", status: "succeeded", completed_tasks: 6, total_tasks: 6, failed_tasks: 0, created_at: "2026-01-01T00:00:00+00:00" },
          ]),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ items: [], total_workers: 8, reported_workers: 0, available_workers: 0, busy_workers: 0 }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Delete" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("renders run detail when plan query present", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/agent-run-plans/plan-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "plan-1",
            status: "running",
            lm_profile_id: "lm-1",
            completed_tasks: 1,
            total_tasks: 6,
            failed_tasks: 0,
            max_workers: 2,
          }),
        });
      }
      if (String(url).includes("/agent-run-plans/plan-1/tasks") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [
              { id: "t1", question_index: 0, attempt_index: 0, status: "running", score: null, worker_id: "worker-1", input_payload: {}, label_payload: {}, prediction_payload: null },
            ],
          }),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [{ worker_id: "worker-a", status: "listening", task_id: null, last_seen: "2026-01-01T00:00:00+00:00" }],
            total_workers: 8,
            reported_workers: 1,
            available_workers: 1,
            busy_workers: 0,
          }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/runs?plan=plan-1"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Run summary")).toBeInTheDocument();
    expect(await screen.findByText(/LM profile: GPT-4o/)).toBeInTheDocument();
    expect(await screen.findByText("Workers")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    expect((await screen.findAllByText("running")).length).toBeGreaterThan(0);
    vi.unstubAllGlobals();
  });

  it("cancels a run from list", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-1", status: "running", completed_tasks: 1, total_tasks: 6, failed_tasks: 0, created_at: "2026-01-01T00:00:00+00:00" },
          ]),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ items: [], total_workers: 8, reported_workers: 0, available_workers: 0, busy_workers: 0 }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/agent-run-plans/plan-1/cancel") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ id: "plan-1", status: "canceled", completed_tasks: 1, total_tasks: 6, failed_tasks: 0, created_at: "2026-01-01T00:00:00+00:00" }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans\/plan-1\/cancel$/), expect.objectContaining({ method: "POST" }));
    expect(await screen.findByText("canceled")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("cancels a run from detail view", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/agent-run-plans/plan-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "plan-1",
            status: "running",
            lm_profile_id: "lm-1",
            completed_tasks: 1,
            total_tasks: 6,
            failed_tasks: 0,
            max_workers: 2,
          }),
        });
      }
      if (String(url).includes("/agent-run-plans/plan-1/tasks") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [] }) });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [], total_workers: 8, reported_workers: 0, available_workers: 0, busy_workers: 0 }) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
      }
      if (String(url).endsWith("/agent-run-plans/plan-1/cancel") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "plan-1",
            status: "canceled",
            lm_profile_id: "lm-1",
            completed_tasks: 1,
            total_tasks: 6,
            failed_tasks: 0,
            max_workers: 2,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(
      <MemoryRouter initialEntries={["/runs?plan=plan-1"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Cancel run" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans\/plan-1\/cancel$/), expect.objectContaining({ method: "POST" }));
    expect(await screen.findByText("canceled")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("renders emoji eval indicator in run item detail drawer", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/agent-run-plans/plan-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "plan-1",
            status: "succeeded",
            lm_profile_id: "lm-1",
            completed_tasks: 1,
            total_tasks: 1,
            failed_tasks: 0,
            max_workers: 1,
          }),
        });
      }
      if (String(url).includes("/agent-run-plans/plan-1/tasks") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [
              {
                id: "t1",
                question_index: 0,
                attempt_index: 0,
                status: "succeeded",
                eval_pass: true,
                score: 1,
                rationale: "exact_match",
                worker_id: "worker-1",
                input_payload: {},
                label_payload: {},
                prediction_payload: {},
              },
            ],
          }),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [], total_workers: 8, reported_workers: 0, available_workers: 0, busy_workers: 0 }) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/runs?plan=plan-1"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("row", { name: /Q1/ }));
    expect(await screen.findByText("Run item detail")).toBeInTheDocument();
    expect(await screen.findAllByText("✅")).toHaveLength(2);

    vi.unstubAllGlobals();
  });

  it("renders emoji eval indicator in the task table", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/agent-run-plans/plan-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "plan-1",
            status: "succeeded",
            lm_profile_id: "lm-1",
            completed_tasks: 2,
            total_tasks: 2,
            failed_tasks: 0,
            max_workers: 1,
          }),
        });
      }
      if (String(url).includes("/agent-run-plans/plan-1/tasks") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [
              { id: "t1", question_index: 0, attempt_index: 0, status: "succeeded", eval_pass: true, score: 1, worker_id: "worker-1", input_payload: {}, label_payload: {}, prediction_payload: {} },
              { id: "t2", question_index: 1, attempt_index: 0, status: "succeeded", eval_pass: false, score: 0, worker_id: "worker-2", input_payload: {}, label_payload: {}, prediction_payload: {} },
            ],
          }),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [], total_workers: 8, reported_workers: 0, available_workers: 0, busy_workers: 0 }) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/runs?plan=plan-1"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("✅")).toBeInTheDocument();
    expect(await screen.findByText("❌")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("deletes a run from list", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-1", status: "succeeded", completed_tasks: 6, total_tasks: 6, failed_tasks: 0, created_at: "2026-01-01T00:00:00+00:00" },
          ]),
        });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ items: [], total_workers: 8, reported_workers: 0, available_workers: 0, busy_workers: 0 }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([]),
        });
      }
      if (String(url).endsWith("/agent-run-plans/plan-1") && init?.method === "DELETE") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({ id: "plan-1", deleted: true }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <RunsPage />
      </MemoryRouter>,
    );

    const deleteButton = await screen.findByRole("button", { name: "Delete" });
    await userEvent.click(deleteButton);

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans\/plan-1$/), expect.objectContaining({ method: "DELETE" }));
    expect(screen.queryByText("plan-1")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("still shows workers when jobs list load fails", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && init?.method === "GET") {
        return Promise.resolve({ ok: false, status: 503, json: vi.fn().mockResolvedValue({}) });
      }
      if (String(url).endsWith("/workers") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [{ worker_id: "worker-z", status: "listening", task_id: null, last_seen: "2026-01-01T00:00:00+00:00" }],
            total_workers: 8,
            reported_workers: 1,
            available_workers: 1,
            busy_workers: 0,
          }),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <RunsPage />
      </MemoryRouter>,
    );

  expect(await screen.findByText("Could not load runs")).toBeInTheDocument();
    expect(await screen.findByText("Workers")).toBeInTheDocument();
    expect(await screen.findByText("worker-z")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
