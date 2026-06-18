import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { EndpointEditorPage, EndpointsPage } from "./EndpointsPage";

describe("EndpointsPage", () => {
  it("renders endpoint list and deletes an endpoint", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "ep-1", name: "Customer API", module_import_id: "mod-1", module_bundle_name: "agentic-chat", pinned_worker_count: 2, key_preview: "abc123" },
        ]) });
      }
      if (String(url).endsWith("/bundle-endpoints/ep-1") && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "ep-1", deleted: true }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <EndpointsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Customer API")).toBeInTheDocument();
    const endpointCard = screen.getByText("Customer API").closest("article");
    expect(endpointCard).toBeTruthy();
    expect(within(endpointCard).getByText(/agentic-chat/)).toBeInTheDocument();
    expect(within(endpointCard).getByText(/Pinned workers 2/)).toBeInTheDocument();
    expect(within(endpointCard).getByRole("button", { name: "Copy curl" })).toBeInTheDocument();
    await userEvent.click(within(endpointCard).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("No endpoints yet")).toBeInTheDocument();
  });

  it("copies curl command from the list page", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "ep-1", name: "Customer API", module_import_id: "mod-1", module_bundle_name: "agentic-chat", pinned_worker_count: 2, key_preview: "abc123" },
        ]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <EndpointsPage />
      </MemoryRouter>,
    );

    const endpointCard = (await screen.findByText("Customer API")).closest("article");
    await userEvent.click(within(endpointCard).getByRole("button", { name: "Copy curl" }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/bundle-endpoints/ep-1/invoke'));
  });

  it("shows endpoints zero state", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <EndpointsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No endpoints yet")).toBeInTheDocument();
  });

  it("creates a new endpoint from the editor page", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "mod-1", bundle_name: "agentic-chat" },
        ]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "lm-1", name: "Primary LM" },
        ]) });
      }
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "ep-2", name: "Public SSE", module_import_id: "mod-1", lm_profile_id: "lm-1", pinned_worker_count: 3, api_key: "bep-new-key" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <EndpointEditorPage />
      </MemoryRouter>,
    );

    await userEvent.type(await screen.findByLabelText("Endpoint name"), "Public SSE");
    await userEvent.selectOptions(screen.getByLabelText("Module bundle"), "mod-1");
    await userEvent.selectOptions(screen.getByLabelText("LM profile"), "lm-1");
    await userEvent.clear(screen.getByLabelText("Pinned workers"));
    await userEvent.type(screen.getByLabelText("Pinned workers"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Save endpoint" }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/bundle-endpoints") && init?.method === "POST");
      expect(createCall).toBeTruthy();
      expect(JSON.parse(createCall[1].body)).toMatchObject({ name: "Public SSE", module_import_id: "mod-1", lm_profile_id: "lm-1", pinned_worker_count: 3 });
    });
  });

  it("loads and updates an existing endpoint", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/modules") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "mod-1", bundle_name: "agentic-chat" },
          { id: "mod-2", bundle_name: "agentic-sales" },
        ]) });
      }
      if (String(url).endsWith("/lm-profiles") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "lm-1", name: "Primary LM" },
          { id: "lm-2", name: "Backup LM" },
        ]) });
      }
      if (String(url).endsWith("/bundle-endpoints/ep-1") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "ep-1", name: "Customer API", module_import_id: "mod-1", lm_profile_id: "lm-1", pinned_worker_count: 2 }) });
      }
      if (String(url).endsWith("/bundle-endpoints/ep-1") && init?.method === "PATCH") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "ep-1", name: "Customer Stream", module_import_id: "mod-2", lm_profile_id: "lm-2", pinned_worker_count: 4 }) });
      }
      if (String(url).endsWith("/bundle-endpoints/ep-1/regenerate-key") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ api_key: "bep-rotated" }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/endpoints/ep-1/edit"]}>
        <Routes>
          <Route path="/endpoints/:endpointId/edit" element={<EndpointEditorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue("Customer API")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Endpoint name"));
    await userEvent.type(screen.getByLabelText("Endpoint name"), "Customer Stream");
    await userEvent.selectOptions(screen.getByLabelText("Module bundle"), "mod-2");
    await userEvent.selectOptions(screen.getByLabelText("LM profile"), "lm-2");
    await userEvent.clear(screen.getByLabelText("Pinned workers"));
    await userEvent.type(screen.getByLabelText("Pinned workers"), "4");
    await userEvent.click(screen.getByRole("button", { name: "Save endpoint" }));
    await userEvent.click(screen.getByRole("button", { name: "Regenerate key" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/bundle-endpoints/ep-1") && init?.method === "PATCH");
      expect(patchCall).toBeTruthy();
      expect(JSON.parse(patchCall[1].body)).toMatchObject({ name: "Customer Stream", module_import_id: "mod-2", lm_profile_id: "lm-2", pinned_worker_count: 4 });
    });
    expect(await screen.findByText("bep-rotated")).toBeInTheDocument();
    expect(screen.getAllByText("Backup LM").length).toBeGreaterThan(0);
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText(/curl -X POST/)).toBeInTheDocument();
    expect(screen.getByText(/curl -N -X POST/)).toBeInTheDocument();
    expect(screen.getAllByText(/bundle-endpoints\/ep-1\/invoke/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bundle-endpoints\/ep-1\/stream/).length).toBeGreaterThan(0);
  });
});
