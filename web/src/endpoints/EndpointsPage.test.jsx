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
      if (String(url).endsWith("/endpoint-workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ total_workers: 3, live_workers: 2, stale_workers: 1, assigned_workers: 2, unassigned_workers: 1, ready_workers: 1, warming_workers: 0, running_workers: 0, failed_workers: 0, items: [
          { worker_id: "endpoint-worker-1", endpoint_id: "ep-1", assigned_endpoint_id: "ep-1", is_live: true, state_label: "Listening", status: "listening", deploy_state: "ready", desired_revision_id: "rev-22222222", warmed_revision_id: "rev-22222222", state_summary: "Ready for traffic on revision rev-2222." },
          { worker_id: "endpoint-worker-2", endpoint_id: "ep-1", assigned_endpoint_id: "ep-1", is_live: false, state_label: "Stale", status: "stale", deploy_state: "revision_mismatch", desired_revision_id: "rev-22222222", warmed_revision_id: "rev-11111111", state_summary: "Assigned endpoint expects revision rev-2222; worker is still warmed on rev-1111." },
          { worker_id: "endpoint-worker-3", endpoint_id: null, assigned_endpoint_id: null, is_live: true, state_label: "Idle", status: "idle", deploy_state: "unassigned", desired_revision_id: null, warmed_revision_id: null, state_summary: "Waiting for an endpoint assignment." },
        ] }) });
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

    expect((await screen.findAllByText("Customer API")).length).toBeGreaterThan(0);
    const endpointCard = screen.getAllByText("Customer API")[0].closest("article");
    expect(endpointCard).toBeTruthy();
    expect(within(endpointCard).getByText(/agentic-chat/)).toBeInTheDocument();
    expect(within(endpointCard).getByText(/Pinned workers 2/)).toBeInTheDocument();
    expect(within(endpointCard).getByRole("button", { name: "Copy curl" })).toBeInTheDocument();
    expect(screen.getByText("Endpoint workers")).toBeInTheDocument();
    expect(screen.getByText(/1 ready of 3 total · 2 live · 1 stale · 2 assigned · 1 unassigned/)).toBeInTheDocument();
    expect(screen.getByText("endpoint-worker-1")).toBeInTheDocument();
    expect(screen.getByText("endpoint-worker-2")).toBeInTheDocument();
    expect(screen.getByText("endpoint-worker-3")).toBeInTheDocument();
    expect(screen.getByText("Ready for traffic on revision rev-2222.")).toBeInTheDocument();
    expect(screen.getByText("Assigned endpoint expects revision rev-2222; worker is still warmed on rev-1111.")).toBeInTheDocument();
    expect(screen.getByText("Waiting for an endpoint assignment.")).toBeInTheDocument();
    expect(screen.getByText("revision_mismatch")).toBeInTheDocument();
    expect(screen.getAllByText("Assigned").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unassigned").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Live").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Stale").length).toBeGreaterThan(0);
    expect(screen.getAllByText("rev-2222").length).toBeGreaterThan(0);
    expect(screen.getAllByText("rev-1111").length).toBeGreaterThan(0);
    await userEvent.click(within(endpointCard).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("No endpoints yet")).toBeInTheDocument();
  });

  it("does not count listening revision mismatches as ready in the worker summary", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "ep-1", name: "Customer API", module_import_id: "mod-1", module_bundle_name: "agentic-chat", pinned_worker_count: 2, key_preview: "abc123" },
        ]) });
      }
      if (String(url).endsWith("/endpoint-workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ total_workers: 3, live_workers: 3, stale_workers: 0, assigned_workers: 2, unassigned_workers: 1, ready_workers: 1, warming_workers: 0, running_workers: 0, failed_workers: 0, items: [
          { worker_id: "endpoint-worker-1", endpoint_id: "ep-1", assigned_endpoint_id: "ep-1", is_live: true, state_label: "Listening", status: "listening", deploy_state: "revision_mismatch", desired_revision_id: "rev-22222222", warmed_revision_id: "rev-11111111", state_summary: "Heartbeat says listening, but desired revision rev-2222 does not match warmed revision rev-1111." },
          { worker_id: "endpoint-worker-2", endpoint_id: "ep-1", assigned_endpoint_id: "ep-1", is_live: true, state_label: "Listening", status: "listening", deploy_state: "ready", desired_revision_id: "rev-22222222", warmed_revision_id: "rev-22222222", state_summary: "Ready for traffic on revision rev-2222." },
          { worker_id: "endpoint-worker-3", endpoint_id: null, assigned_endpoint_id: null, is_live: true, state_label: "Idle", status: "idle", deploy_state: "unassigned", desired_revision_id: null, warmed_revision_id: null, state_summary: "Waiting for an endpoint assignment." },
        ] }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <EndpointsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/1 ready of 3 total · 3 live · 0 stale · 2 assigned · 1 unassigned/)).toBeInTheDocument();
    expect(screen.getByText("Heartbeat says listening, but desired revision rev-2222 does not match warmed revision rev-1111.")).toBeInTheDocument();
  });

  it("shows the authoritative registry roster when one worker stops heartbeating", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([
          { id: "ep-1", name: "Customer API", module_import_id: "mod-1", module_bundle_name: "agentic-chat", pinned_worker_count: 2, key_preview: "abc123" },
        ]) });
      }
      if (String(url).endsWith("/endpoint-workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ total_workers: 2, reported_workers: 2, live_workers: 1, stale_workers: 1, assigned_workers: 2, unassigned_workers: 0, ready_workers: 1, warming_workers: 0, running_workers: 0, failed_workers: 0, items: [
          { worker_id: "endpoint-worker-1", endpoint_id: "ep-1", assigned_endpoint_id: "ep-1", is_live: true, state_label: "Listening", status: "listening", deploy_state: "ready", desired_revision_id: "rev-22222222", warmed_revision_id: "rev-22222222", state_summary: "Ready for traffic on revision rev-2222." },
          { worker_id: "endpoint-worker-2", endpoint_id: "ep-1", assigned_endpoint_id: "ep-1", is_live: false, state_label: "Stale", status: "stale", deploy_state: "revision_mismatch", desired_revision_id: "rev-33333333", warmed_revision_id: "rev-11111111", state_summary: "Assigned endpoint expects revision rev-3333; worker is still warmed on rev-1111.", last_seen: "2025-12-31T23:59:00+00:00" },
        ] }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <EndpointsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/1 ready of 2 total · 1 live · 1 stale · 2 assigned · 0 unassigned/)).toBeInTheDocument();
    expect(screen.getByText("endpoint-worker-1")).toBeInTheDocument();
    expect(screen.getByText("endpoint-worker-2")).toBeInTheDocument();
    expect(screen.queryByText("ephemeral-worker-99")).not.toBeInTheDocument();
    expect(screen.getByText("Assigned endpoint expects revision rev-3333; worker is still warmed on rev-1111.")).toBeInTheDocument();
    expect(screen.getAllByText("Stale").length).toBeGreaterThan(0);
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
      if (String(url).endsWith("/endpoint-workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [], total_workers: 0 }) });
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

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`${window.location.origin}/api/bundle-endpoints/ep-1/invoke`));
  });

  it("shows endpoints zero state", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/bundle-endpoints") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/endpoint-workers") && init?.method === "GET") {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ items: [], total_workers: 0 }) });
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
    expect(screen.getByText("No endpoint workers registered yet.")).toBeInTheDocument();
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
