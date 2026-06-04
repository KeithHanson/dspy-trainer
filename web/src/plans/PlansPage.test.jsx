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
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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
            { id: "mod-1", bundle_name: "policy-bot", bundle_version: "1.0.0", validation_status: "passed", source_ref: "/tmp/checkouts/policy-bot" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
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
    await userEvent.selectOptions(screen.getByLabelText("Runtime model profile"), "lm-1");
    await userEvent.type(screen.getByPlaceholderText("Input prompt"), "What is the refund policy?");
    await userEvent.type(screen.getByPlaceholderText("Expected answer"), "Provide refund window details");
    await userEvent.click(screen.getByRole("button", { name: "Save & run" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-plans$/), expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans$/), expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans\/run-plan-1\/enqueue$/), expect.objectContaining({ method: "POST" }));

    const evalPlanSave = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/evaluation-plans") && init?.method === "POST");
    expect(evalPlanSave).toBeTruthy();
    expect(JSON.parse(evalPlanSave[1].body).lm_profile_id).toBe("lm-1");
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
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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

  it("requires LM profile before saving", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "policy-bot", bundle_version: "1.0.0", validation_status: "passed", source_ref: "/tmp/checkouts/policy-bot" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
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

    await userEvent.type(await screen.findByLabelText("Plan name"), "No LM Profile");
    await userEvent.type(screen.getByPlaceholderText("Input prompt"), "Question?");
    await userEvent.type(screen.getByPlaceholderText("Expected answer"), "Answer");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Select an LM profile.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-plans$/), expect.objectContaining({ method: "POST" }));
  });

  it("updates existing plan when editing", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "policy-bot", bundle_version: "1.0.0", validation_status: "passed", source_ref: "/tmp/checkouts/policy-bot" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
      }
      if (String(url).endsWith("/evaluation-plans/plan-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "plan-1",
            name: "Existing",
            runs_per_question: 1,
            max_workers: 1,
            module_import_id: "mod-1",
            lm_profile_id: "lm-1",
            eval_inputs: [{ input: { question: "q1" }, label: { expected: "a1" } }],
          }),
        });
      }
      if (String(url).endsWith("/evaluation-plans/plan-1") && init?.method === "PATCH") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "plan-1" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/plans?new=1&id=plan-1"]}>
        <PlansPage />
      </MemoryRouter>,
    );

    await screen.findByDisplayValue("Existing");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-plans\/plan-1$/), expect.objectContaining({ method: "PATCH" }));
  });

  it("generates eval rows via LLM preview and inserts them on approval", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "policy-bot", bundle_version: "1.0.0", validation_status: "passed", source_ref: "/tmp/checkouts/policy-bot" },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
      }
      if (String(url).endsWith("/evaluation-plans/generate-rows") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            items: [
              { input: { question: "How long do refunds take?" }, label: { expected: "Explain the standard refund timeline." } },
              { input: { question: "What if an item arrives damaged?" }, label: { expected: "Explain the damaged-item refund flow." } },
            ],
            attempts: 1,
          }),
        });
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

    await userEvent.selectOptions(await screen.findByLabelText("Runtime model profile"), "lm-1");
    await userEvent.click(screen.getByRole("button", { name: "Generate with LLM" }));
    await userEvent.selectOptions(screen.getByLabelText("LM profile for generation"), "lm-1");
    await userEvent.type(screen.getByLabelText("What data do you need?"), "Generate refund cases");
    await userEvent.click(screen.getByRole("button", { name: "Generate preview" }));

    expect(await screen.findByText("How long do refunds take?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve + insert rows" }));

    expect(await screen.findByDisplayValue("How long do refunds take?")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("Explain the standard refund timeline.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-plans\/generate-rows$/), expect.objectContaining({ method: "POST" }));

    vi.unstubAllGlobals();
  });
});
