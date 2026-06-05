import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { DatasetEditorPage, DatasetsPage } from "./DatasetsPage";

describe("DatasetsPage", () => {
  it("renders dataset list and duplicates a dataset", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/evaluation-datasets") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "dataset-1",
              name: "Support regression",
              bundle_name: "policy-bot",
              record_count: 2,
              input_keys: ["ticket", "history"],
              label_keys: ["category", "reply"],
              updated_at: "2026-01-01T00:00:00+00:00",
            },
          ]),
        });
      }
      if (String(url).endsWith("/evaluation-datasets/dataset-1/duplicate") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "dataset-2" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/datasets"]}>
        <DatasetsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Support regression")).toBeInTheDocument();
    expect(screen.getByText("Bundle: policy-bot")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/evaluation-datasets\/dataset-1\/duplicate$/), expect.objectContaining({ method: "POST" }));
    vi.unstubAllGlobals();
  });

  it("saves a new dataset from the editor", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-1",
              bundle_name: "policy-bot",
              bundle_version: "1.0.0",
              validation_status: "passed",
              evaluation_contract: {
                input_fields: [{ key: "ticket", label: "Ticket", required: true }],
                label_fields: [{ key: "expected", label: "Expected response", required: true }],
                input_template: { ticket: "" },
                label_template: { expected: "" },
              },
            },
          ]),
        });
      }
      if (String(url).endsWith("/evaluation-datasets") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "dataset-1" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/datasets/new"]}>
        <Routes>
          <Route path="/datasets/new" element={<DatasetEditorPage />} />
          <Route path="/datasets" element={<div>datasets landing</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.type(await screen.findByLabelText("Dataset name"), "Support dataset");
    fireEvent.change(screen.getByPlaceholderText(/"ticket": ""/), { target: { value: '{"ticket":"I was charged twice."}' } });
    fireEvent.change(screen.getByPlaceholderText(/"expected": ""/), { target: { value: '{"expected":"Acknowledge and request receipt."}' } });
    await userEvent.click(screen.getByRole("button", { name: "Save dataset" }));

    const saveCall = fetchMock.mock.calls.find(([url, request]) => String(url).endsWith("/evaluation-datasets") && request?.method === "POST");
    expect(saveCall).toBeTruthy();
    expect(JSON.parse(saveCall[1].body)).toMatchObject({
      name: "Support dataset",
      module_import_id: "mod-1",
      records: [{ input: { ticket: "I was charged twice." }, label: { expected: "Acknowledge and request receipt." } }],
    });
    vi.unstubAllGlobals();
  });

  it("duplicates the selected dataset item in the editor", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-1",
              bundle_name: "policy-bot",
              bundle_version: "1.0.0",
              validation_status: "passed",
              evaluation_contract: {
                input_fields: [{ key: "ticket", label: "Ticket", required: true }],
                label_fields: [{ key: "expected", label: "Expected response", required: true }],
                input_template: { ticket: "" },
                label_template: { expected: "" },
              },
            },
          ]),
        });
      }
      if (String(url).endsWith("/evaluation-datasets/dataset-1") && init?.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "dataset-1",
            name: "Support dataset",
            module_import_id: "mod-1",
            records: [{ id: "item-1", input: { ticket: "I was charged twice." }, label: { expected: "Acknowledge and request receipt." } }],
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/datasets/dataset-1/edit"]}>
        <Routes>
          <Route path="/datasets/:datasetId/edit" element={<DatasetEditorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue(/"ticket": "I was charged twice\."/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Duplicate" }));

    expect(screen.getByText("Input 2")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
