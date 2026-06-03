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
    expect((await screen.findAllByText("running")).length).toBeGreaterThan(0);
    vi.unstubAllGlobals();
  });

it("deletes a run from list", async () => {
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
