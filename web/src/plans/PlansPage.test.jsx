import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { PlansPage } from "./PlansPage";

describe("PlansPage", () => {
  it("renders plan list from API", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/evaluation-plans") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "plan-1",
              name: "Refund regression",
              eval_inputs: [{}, {}],
              runs_per_question: 3,
              max_workers: 4,
            },
          ]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/plans"]}>
        <PlansPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Refund regression")).toBeInTheDocument();
    expect(screen.getByText("2 questions x 3 runs = 6 tasks · 4 workers")).toBeInTheDocument();
  });

  it("saves and runs a plan from builder", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "policy-bot", bundle_version: "1.0.0", validation_status: "passed", source_ref: "bundle.zip" },
          ]),
        });
      }
      if (String(url).endsWith("/evaluation-plans") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "eval-1" }) });
      }
      if (String(url).endsWith("/agent-run-plans") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "run-plan-1" }) });
      }
      if (String(url).endsWith("/agent-run-plans/run-plan-1/enqueue") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ status: "queued" }) });
      }
      if (String(url).endsWith("/evaluation-plans") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/plans?new=1"]}>
        <PlansPage />
      </MemoryRouter>,
    );

    await userEvent.type(await screen.findByLabelText("Plan name"), "Triage v4");
    await userEvent.type(screen.getByPlaceholderText("Input prompt"), "What is the refund policy?");
    await userEvent.type(screen.getByPlaceholderText("Expected answer"), "Provide refund window details");
    await userEvent.click(screen.getByRole("button", { name: "Save & run" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-plans$/), expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans$/), expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans\/run-plan-1\/enqueue$/), expect.objectContaining({ method: "POST" }));
  });

  it("shows edit and delete actions and deletes a plan", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/evaluation-plans") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "plan-1",
              name: "Refund regression",
              eval_inputs: [{}, {}],
              runs_per_question: 3,
              max_workers: 4,
            },
          ]),
        });
      }
      if (String(url).endsWith("/evaluation-plans/plan-1") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "plan-1", deleted: true }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/plans"]}>
        <PlansPage />
      </MemoryRouter>,
    );

    await screen.findByText("Refund regression");
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-plans\/plan-1$/), expect.objectContaining({ method: "DELETE" }));
  });
});
