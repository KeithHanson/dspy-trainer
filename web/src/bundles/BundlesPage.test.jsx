import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { BundlesPage } from "./BundlesPage";

function renderBundlesApp(initialEntries = ["/bundles"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/bundles" element={<BundlesPage />} />
        <Route path="/bundles/:moduleId" element={<BundlesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BundlesPage", () => {
  it("downloads the example bundle starter", async () => {
    const blob = new Blob(["zip"], { type: "application/zip" });
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith("/samples/module-bundle")) {
        return Promise.resolve({ ok: true, blob: vi.fn().mockResolvedValue(blob) });
      }
      if (String(url).endsWith("/modules")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    if (!URL.createObjectURL) {
      URL.createObjectURL = vi.fn();
    }
    if (!URL.revokeObjectURL) {
      URL.revokeObjectURL = vi.fn();
    }
    const urlMock = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    const revokeMock = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(screen.getByRole("button", { name: "Download example" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/samples\/module-bundle$/), { method: "GET" });
    expect(urlMock).toHaveBeenCalledTimes(1);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeMock).toHaveBeenCalledWith("blob:test");

    vi.unstubAllGlobals();
    urlMock.mockRestore();
    revokeMock.mockRestore();
    anchorClick.mockRestore();
  });

  it("shows github import panel when import query is present", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) })));
    renderBundlesApp(["/bundles?import=1"]);

    expect(screen.getByText("Step 2: Import and validate GitHub bundle")).toBeInTheDocument();
    expect(screen.queryByText("Example bundle")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Bundle zip")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("GitHub personal access token")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/GitHub access is configured/)).toBeInTheDocument());
    vi.unstubAllGlobals();
  });

  it("submits github import flow and renders diagnostics", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules/import")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "mod-1", status: "imported" }) });
      }
      if (String(url).endsWith("/modules/mod-1")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-1",
            bundle_name: "repo-bundle",
            bundle_version: "1.2.3",
            github_repo_url: "https://github.com/example/repo-bundle",
            github_branch: "main",
            github_subpath: "bundles/support",
            current_commit_sha: "abc12345",
            validation_status: "failed",
            diagnostics: [{ severity: "error", code: "module_missing", message: "module.py missing" }],
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp(["/bundles?import=1"]);

    await userEvent.type(screen.getByLabelText("GitHub repository URL"), "https://github.com/example/repo-bundle");
    await userEvent.clear(screen.getByLabelText("Branch"));
    await userEvent.type(screen.getByLabelText("Branch"), "main");
    await userEvent.type(screen.getByLabelText("Bundle subfolder (optional)"), "bundles/support");
    fireEvent.submit(screen.getByRole("button", { name: "Import + validate" }).closest("form"));

    await waitFor(() => expect(screen.getByText("Validation result")).toBeInTheDocument());
    expect(screen.getByText(/module_missing: module.py missing/)).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/github.com\/example\/repo-bundle/)).toBeInTheDocument();
    expect(screen.getByText("bundles/support")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("renders github import validation errors", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules/import")) {
        return Promise.resolve({ ok: false, json: vi.fn().mockResolvedValue({ error: "Validation failed with 1 error." }) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp(["/bundles?import=1"]);

    await userEvent.type(screen.getByLabelText("GitHub repository URL"), "https://github.com/example/not-a-bundle");
    await userEvent.clear(screen.getByLabelText("Branch"));
    await userEvent.type(screen.getByLabelText("Branch"), "main");
    fireEvent.submit(screen.getByRole("button", { name: "Import + validate" }).closest("form"));

    await waitFor(() => expect(screen.getByText("Import failed")).toBeInTheDocument());
    expect(screen.getByText(/Validation failed with 1 error/)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("shows missing github configuration message", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: false } }) });
      }
      if (String(url).endsWith("/modules")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp(["/bundles?import=1"]);

    await waitFor(() => expect(screen.getByText(/GitHub access is not configured/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Import + validate" })).toBeDisabled();

    vi.unstubAllGlobals();
  });

  it("handles non-array diagnostics when viewing saved bundle", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules/mod-2/sync-status")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ sync_status: "synced", current_commit_sha: "abc12345", upstream_commit_sha: "abc12345" }) });
      }
      if (String(url).endsWith("/modules/mod-2")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-2",
            bundle_name: "support-triage-agent",
            github_repo_url: "https://github.com/example/support-triage-agent",
            github_branch: "main",
            sync_status: "synced",
            validation_status: "passed",
            status: "imported",
            diagnostics: { unexpected: true },
          }),
        });
      }
      if (String(url).endsWith("/modules/mod-2/files")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ "module.py": "print('support')" }) });
      }
      if (String(url).endsWith("/modules/mod-2/revisions")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-2",
              bundle_name: "support-triage-agent",
              github_repo_url: "https://github.com/example/support-triage-agent",
              github_branch: "main",
              sync_status: "synced",
              validation_status: "passed",
              status: "imported",
              diagnostics: { unexpected: true },
            },
          ]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    expect(await screen.findByLabelText("Bundle name")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("reloads bundle files when a file button is clicked", async () => {
    let fileFetchCount = 0;
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules/mod-3/sync-status")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ sync_status: "synced", current_commit_sha: "abc12345", upstream_commit_sha: "abc12345" }) });
      }
      if (String(url).endsWith("/modules/mod-3")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-3",
            bundle_name: "reload-test",
            bundle_version: "0.1.0",
            validation_status: "passed",
            status: "imported",
            diagnostics: [],
          }),
        });
      }
      if (String(url).endsWith("/modules/mod-3/revisions")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-3",
              bundle_name: "reload-test",
              bundle_version: "0.1.0",
              validation_status: "passed",
              status: "imported",
              diagnostics: [],
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-3/files")) {
        fileFetchCount += 1;
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            "module.py": `print(${fileFetchCount})`,
            "metric.py": `metric_${fileFetchCount}`,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("button", { name: "Files" }));
    expect(await screen.findByText("print(1)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "metric.py" }));

    await waitFor(() => expect(fileFetchCount).toBe(1));
    expect(await screen.findByText("metric_1")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("renders sync status, revision history, and manual sync action", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-sync",
              bundle_name: "repo-bundle",
              bundle_version: "1.2.3",
              github_repo_url: "https://github.com/example/repo-bundle",
              github_branch: "main",
              current_commit_sha: "abc12345",
              last_synced_at: "2026-06-04T17:00:00+00:00",
              sync_status: "behind",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-sync/files")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ "module.py": "print('sync')" }) });
      }
      if (String(url).endsWith("/modules/mod-sync")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-sync",
            bundle_name: "repo-bundle",
            bundle_version: "1.2.3",
            github_repo_url: "https://github.com/example/repo-bundle",
            github_branch: "main",
            current_commit_sha: "abc12345",
            last_synced_at: "2026-06-04T17:00:00+00:00",
            sync_status: "behind",
            validation_status: "passed",
            status: "validated",
            diagnostics: [],
          }),
        });
      }
      if (String(url).endsWith("/modules/mod-sync/sync-status") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            module_id: "mod-sync",
            sync_status: "behind",
            current_commit_sha: "abc12345",
            upstream_commit_sha: "def67890",
          }),
        });
      }
      if (String(url).endsWith("/modules/mod-sync/revisions")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "rev-2",
              commit_sha: "def67890",
              bundle_version: "1.2.4",
              source_event: "sync",
              created_at: "2026-06-04T17:05:00+00:00",
            },
            {
              id: "rev-1",
              commit_sha: "abc12345",
              bundle_version: "1.2.3",
              source_event: "import",
              created_at: "2026-06-04T17:00:00+00:00",
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-sync/sync") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            module_id: "mod-sync",
            sync_status: "synced",
            current_commit_sha: "def67890",
            upstream_commit_sha: "def67890",
            synced: true,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("button", { name: "Sync" }));
    expect(await screen.findByText(/Sync status:/)).toBeInTheDocument();
    expect(await screen.findByText(/behind/)).toBeInTheDocument();
    expect(await screen.findAllByText(/def67890/)).toHaveLength(2);
    expect(await screen.findByText(/Revision history/)).toBeInTheDocument();
    expect(await screen.findByText(/v1.2.4/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Sync bundle" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/modules\/mod-sync\/sync$/), expect.objectContaining({ method: "POST" })));

    vi.unstubAllGlobals();
  });

  it("saves module environment entries from the environment tab", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-env",
              bundle_name: "agentic-chat",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
              environment_entries: [],
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-env/files")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ "module.py": "print('x')" }) });
      }
      if (String(url).endsWith("/modules/mod-env")) {
        if (init?.method === "PATCH") {
          return Promise.resolve({
            ok: true,
            json: vi.fn().mockResolvedValue({
              id: "mod-env",
              bundle_name: "agentic-chat",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
              environment_entries: [
                { key: "AGENTIC_CHAT_ENDPOINT", value: "https://example.test", is_secret: true },
              ],
            }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-env",
            bundle_name: "agentic-chat",
            validation_status: "passed",
            status: "validated",
            diagnostics: [],
            environment_entries: [],
          }),
        });
      }
      if (String(url).endsWith("/modules/mod-env/sync-status")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ sync_status: "synced" }) });
      }
      if (String(url).endsWith("/modules/mod-env/revisions")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("button", { name: "Environment" }));
    await userEvent.click(screen.getByRole("button", { name: "Add variable" }));
    await userEvent.type(screen.getByLabelText("Environment key 1"), "AGENTIC_CHAT_ENDPOINT");
    await userEvent.type(screen.getByLabelText("Environment value 1"), "https://example.test");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Save environment" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/modules\/mod-env$/),
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          environment_entries: [
            { key: "AGENTIC_CHAT_ENDPOINT", value: "https://example.test", is_secret: true },
          ],
        }),
      }),
    ));

    vi.unstubAllGlobals();
  });

  it("parses dotenv-style paste into environment entries", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-paste",
              bundle_name: "agentic-chat",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
              environment_entries: [],
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-paste")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-paste",
            bundle_name: "agentic-chat",
            validation_status: "passed",
            status: "validated",
            diagnostics: [],
            environment_entries: [],
          }),
        });
      }
      if (String(url).endsWith("/modules/mod-paste/files")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ "module.py": "print('x')" }) });
      }
      if (String(url).endsWith("/modules/mod-paste/sync-status")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ sync_status: "synced" }) });
      }
      if (String(url).endsWith("/modules/mod-paste/revisions")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("button", { name: "Environment" }));
    fireEvent.paste(screen.getByText(/Paste dotenv-style lines anywhere in this tab/i), {
      clipboardData: {
        getData: () => 'AZURE_AI_TENANT_ID="f97fa841-8abc-491f-9902-26c8312eade0"\n\nAZURE_SEARCH_ENDPOINT="https://abatix-search.search.windows.net"\nAZURE_SEARCH_INDEX="ai-query-markdown-search"',
      },
    });

    expect(await screen.findByDisplayValue("AZURE_AI_TENANT_ID")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("f97fa841-8abc-491f-9902-26c8312eade0")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("AZURE_SEARCH_ENDPOINT")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("https://abatix-search.search.windows.net")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("AZURE_SEARCH_INDEX")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("ai-query-markdown-search")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
