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
              dataset_id: "dataset-1",
              dataset_name: "Support dataset",
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
    expect(screen.getByText("Dataset: Support dataset")).toBeInTheDocument();
    expect(screen.getByText("3 runs per input · 4 workers")).toBeInTheDocument();
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
      if (String(url).endsWith("/evaluation-datasets") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "dataset-1", name: "Support dataset", module_import_id: "mod-1", record_count: 2, input_keys: ["ticket"], label_keys: ["expected"] },
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
    await userEvent.click(screen.getByRole("button", { name: "Save & run" }));

    const evalPlanSave = fetchMock.mock.calls.find(([url, request]) => String(url).endsWith("/evaluation-plans") && request?.method === "POST");
    expect(evalPlanSave).toBeTruthy();
    expect(JSON.parse(evalPlanSave[1].body)).toMatchObject({
      lm_profile_id: "lm-1",
      module_import_id: "mod-1",
      dataset_id: "dataset-1",
    });

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans$/), expect.objectContaining({ method: "POST" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/agent-run-plans\/run-plan-1\/enqueue$/), expect.objectContaining({ method: "POST" }));
  });

  it("shows edit and delete actions and deletes a plan", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/evaluation-plans") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "plan-1", name: "Refund regression", dataset_id: "dataset-1", dataset_name: "Support dataset", runs_per_question: 3, max_workers: 4 },
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
      if (String(url).endsWith("/evaluation-datasets") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "dataset-1", name: "Support dataset", module_import_id: "mod-1", record_count: 2, input_keys: ["ticket"], label_keys: ["expected"] },
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
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Select an LM profile.")).toBeInTheDocument();
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
      if (String(url).endsWith("/evaluation-datasets") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "dataset-1", name: "Support dataset", module_import_id: "mod-1", record_count: 2, input_keys: ["ticket"], label_keys: ["expected"] },
            { id: "dataset-2", name: "Support dataset v2", module_import_id: "mod-1", record_count: 3, input_keys: ["ticket"], label_keys: ["expected"] },
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
            dataset_id: "dataset-1",
            lm_profile_id: "lm-1",
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
    expect(screen.getByText("Support dataset")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Support dataset v2"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    const patchCall = fetchMock.mock.calls.find(([url, request]) => String(url).endsWith("/evaluation-plans/plan-1") && request?.method === "PATCH");
    expect(patchCall).toBeTruthy();
    expect(JSON.parse(patchCall[1].body).dataset_id).toBe("dataset-2");
  });

  it("filters datasets to the selected bundle", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "mod-1", bundle_name: "policy-bot", bundle_version: "1.0.0", validation_status: "passed", source_ref: "/tmp/checkouts/policy-bot" },
            { id: "mod-2", bundle_name: "other-bot", bundle_version: "1.2.0", validation_status: "passed", source_ref: "/tmp/checkouts/other-bot" },
          ]),
        });
      }
      if (String(url).endsWith("/evaluation-datasets") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "dataset-1", name: "Support dataset", module_import_id: "mod-1", record_count: 2, input_keys: ["ticket"], label_keys: ["expected"] },
            { id: "dataset-2", name: "Other dataset", module_import_id: "mod-2", record_count: 1, input_keys: ["prompt"], label_keys: ["expected"] },
          ]),
        });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([{ id: "lm-1", name: "GPT-4o" }]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/plans?new=1"]}>
        <PlansPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Support dataset")).toBeInTheDocument();
    expect(screen.queryByText("Other dataset")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("other-bot"));
    expect(await screen.findByText("Other dataset")).toBeInTheDocument();
    expect(screen.queryByText("Support dataset")).not.toBeInTheDocument();
  });
});
