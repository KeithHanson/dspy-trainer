import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function deferredResponse() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

describe("BundlesPage", () => {
  it("downloads the example bundle starter", async () => {
    const blob = new Blob(["zip"], { type: "application/zip" });
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("/samples/module-bundle?sample=it-ticket-triage")) {
        return Promise.resolve({
          ok: true,
          headers: { get: vi.fn().mockReturnValue('attachment; filename="it-ticket-triage.zip"') },
          blob: vi.fn().mockResolvedValue(blob),
        });
      }
      if (String(url).includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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

    await userEvent.click(screen.getByText("Download sample"));
    await userEvent.click(screen.getByRole("button", { name: /IT ticket triage/i }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/samples\/module-bundle\?sample=it-ticket-triage$/), { method: "GET" });
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
      if (String(url).includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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
      if (String(url).includes("/agent-run-plans?")) {
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
      if (String(url).includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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
      if (String(url).includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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
      if (String(url).includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
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

  it("renders an eval trend sparkline for saved bundles with scored runs", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            { id: "run-1", module_import_id: "mod-trend", average_score: 0.52, score_pass_threshold: 0.8, created_at: "2026-06-01T10:00:00+00:00" },
            { id: "run-2", module_import_id: "mod-trend", average_score: 0.78, score_pass_threshold: 0.8, created_at: "2026-06-02T10:00:00+00:00" },
            { id: "run-3", module_import_id: "mod-trend", average_score: 0.91, score_pass_threshold: 0.8, created_at: "2026-06-03T10:00:00+00:00" },
          ]),
        });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-trend",
              bundle_name: "agentic-chat",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
            },
          ]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    expect(await screen.findByLabelText("Recent eval scores for agentic-chat")).toBeInTheDocument();
    expect(await screen.findByText("91%")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("renders a stacked empty eval trend state when a bundle has no scored runs", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([]),
        });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-empty",
              bundle_name: "agentic-chat",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
            },
          ]),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    const emptyStateLabel = await screen.findByText("No evals yet");
    expect(emptyStateLabel.closest(".bundles-sparkline-card-empty")).toBeInTheDocument();
    expect(screen.getByText("Eval trend")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("syncs a bundle from the list and shows a success notice", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).includes("/agent-run-plans?") && (!init || init.method === "GET")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-sync-list",
              bundle_name: "agentic-chat",
              validation_status: "passed",
              status: "validated",
              diagnostics: [],
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-sync-list/sync") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            module_id: "mod-sync-list",
            sync_status: "synced",
            synced: true,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Sync" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/modules\/mod-sync-list\/sync$/), expect.objectContaining({ method: "POST" })));
    expect(await screen.findByText("agentic-chat synced successfully.")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("builds from the listing, streams sanitized output to ready, and reopens the current build", async () => {
    const postBuild = deferredResponse();
    let statusRequests = 0;
    const baseModule = {
      id: "mod-build",
      bundle_name: "image-agent",
      current_revision_id: "revision-current",
      validation_status: "passed",
      status: "validated",
      image_build: { eligible: true, status: "enqueue_failed", current_build: null },
    };
    const queuedBuild = {
      id: "build-1",
      revision_id: "revision-current",
      generation: 1,
      status: "queued",
      queued_at: "2026-09-26T10:00:00Z",
      updated_at: "2026-09-26T10:00:00Z",
    };
    const fetchMock = vi.fn((url, init) => {
      const value = String(url);
      if (value.includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([]) });
      }
      if (value.endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([baseModule]) });
      }
      if (value.endsWith("/modules/mod-build/revision-image-builds") && init?.method === "POST") {
        return postBuild.promise;
      }
      if (value.includes("/revision-image-builds/build-1/logs?")) {
        const text = statusRequests >= 2
          ? "TOKEN=final-secret\nimage ready"
          : "Authorization: Bearer live-secret\nbuilding layer";
        return Promise.resolve({
          ok: true,
          status: 200,
          json: vi.fn().mockResolvedValue({ text, total_bytes: text.length, next_offset: null }),
        });
      }
      if (value.endsWith("/revision-image-builds/build-1") && (!init || init.method === "GET")) {
        statusRequests += 1;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: vi.fn().mockResolvedValue({
            ...queuedBuild,
            status: statusRequests === 1 ? "building" : "ready",
            started_at: "2026-09-26T10:00:01Z",
            finished_at: statusRequests === 1 ? null : "2026-09-26T10:00:03Z",
            updated_at: `2026-09-26T10:00:0${statusRequests}Z`,
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderBundlesApp();

    expect(await screen.findByText("no build")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(screen.getByRole("dialog", { name: "Image build · image-agent" })).toBeInTheDocument();
    expect(screen.getByText("Queuing image build...")).toBeInTheDocument();

    await act(async () => {
      postBuild.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue(queuedBuild) });
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getAllByText("building").length).toBeGreaterThan(0));
    const liveOutput = await screen.findByLabelText("Build output for build-1");
    expect(liveOutput).toHaveTextContent("Authorization: [REDACTED]");
    expect(liveOutput).not.toHaveTextContent("live-secret");

    await waitFor(() => expect(screen.getAllByText("ready").length).toBeGreaterThanOrEqual(2), { timeout: 3_500 });
    const finalOutput = screen.getByLabelText("Build output for build-1");
    expect(finalOutput).toHaveTextContent("TOKEN=[REDACTED]");
    expect(finalOutput).toHaveTextContent("image ready");
    expect(finalOutput).not.toHaveTextContent("final-secret");

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "View build" }));
    expect(await screen.findByRole("dialog", { name: "Image build · image-agent" })).toBeInTheDocument();
    expect(await screen.findByText("Generation 1")).toBeInTheDocument();
    vi.unstubAllGlobals();
  }, 8_000);

  it("keeps the module row live when the modal closes before enqueue resolves", async () => {
    const postBuild = deferredResponse();
    let moduleRequests = 0;
    const baseModule = {
      id: "mod-close",
      bundle_name: "close-agent",
      current_revision_id: "revision-close",
      validation_status: "passed",
      status: "validated",
      image_build: { eligible: true, current_build: null },
    };
    const queuedBuild = {
      id: "build-close",
      revision_id: "revision-close",
      generation: 1,
      status: "queued",
      updated_at: "2026-09-26T10:30:00Z",
    };
    const readyBuild = {
      ...queuedBuild,
      status: "ready",
      finished_at: "2026-09-26T10:30:03Z",
      updated_at: "2026-09-26T10:30:03Z",
    };
    const fetchMock = vi.fn((url, init) => {
      const value = String(url);
      if (value.includes("/agent-run-plans?")) {
        return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([]) });
      }
      if (value.endsWith("/modules") && (!init || init.method === "GET")) {
        moduleRequests += 1;
        const module = moduleRequests === 1
          ? baseModule
          : { ...baseModule, image_build: { eligible: true, status: "ready", current_build: readyBuild } };
        return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([module]) });
      }
      if (value.endsWith("/modules/mod-close/revision-image-builds") && init?.method === "POST") {
        return postBuild.promise;
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderBundlesApp();

    const row = (await screen.findByText("close-agent")).closest(".bundles-saved-row");
    await userEvent.click(within(row).getByRole("button", { name: "Build" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => {
      postBuild.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue(queuedBuild) });
      await Promise.resolve();
    });
    await waitFor(() => expect(within(row).getByText("queued")).toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await waitFor(() => expect(within(row).getByText("ready")).toBeInTheDocument(), { timeout: 3_500 });
    expect(moduleRequests).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  }, 5_000);

  it("renders failed build details and actionable ineligible errors in the immediate modal", async () => {
    const module = {
      id: "mod-failed",
      bundle_name: "failure-agent",
      current_revision_id: "revision-failed",
      validation_status: "passed",
      status: "validated",
      image_build: { eligible: true, current_build: null },
    };
    let postCount = 0;
    const failedBuild = {
      id: "build-failed",
      revision_id: "revision-failed",
      generation: 2,
      status: "failed",
      queued_at: "2026-09-26T11:00:00Z",
      finished_at: "2026-09-26T11:00:04Z",
      updated_at: "2026-09-26T11:00:04Z",
      failure_reason: "API_KEY=do-not-render\nDocker build failed",
    };
    const fetchMock = vi.fn((url, init) => {
      const value = String(url);
      if (value.includes("/agent-run-plans?")) return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([]) });
      if (value.endsWith("/modules") && (!init || init.method === "GET")) return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([module]) });
      if (value.endsWith("/modules/mod-failed/revision-image-builds") && init?.method === "POST") {
        postCount += 1;
        return postCount === 1
          ? Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue(failedBuild) })
          : Promise.resolve({ ok: false, status: 409, json: vi.fn().mockResolvedValue({ error: "Sync and validate the current revision before building" }) });
      }
      if (value.endsWith("/revision-image-builds/build-failed")) return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue(failedBuild) });
      if (value.includes("/revision-image-builds/build-failed/logs?")) return Promise.resolve({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue({ text: "TOKEN=hidden\nfailed command", total_bytes: 30, next_offset: null }),
      });
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderBundlesApp();

    await userEvent.click(await screen.findByRole("button", { name: "Build" }));
    expect(await screen.findByText(/Docker build failed/)).toBeInTheDocument();
    expect(screen.queryByText(/do-not-render/)).not.toBeInTheDocument();
    const output = await screen.findByLabelText("Build output for build-failed");
    expect(output).toHaveTextContent("TOKEN=[REDACTED]");
    expect(output).not.toHaveTextContent("hidden");

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(screen.getByRole("dialog", { name: "Image build · failure-agent" })).toBeInTheDocument();
    expect(await screen.findByText("Sync and validate the current revision before building")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("ignores late build responses after selecting another module or closing the modal", async () => {
    const firstPost = deferredResponse();
    const secondStatus = deferredResponse();
    const modules = [
      { id: "mod-a", bundle_name: "agent-a", current_revision_id: "revision-a", validation_status: "passed", status: "validated", image_build: { current_build: null } },
      { id: "mod-b", bundle_name: "agent-b", current_revision_id: "revision-b", validation_status: "passed", status: "validated", image_build: { current_build: null } },
    ];
    const buildB = { id: "build-b", revision_id: "revision-b", generation: 1, status: "queued", updated_at: "2026-09-26T12:00:00Z" };
    const fetchMock = vi.fn((url, init) => {
      const value = String(url);
      if (value.includes("/agent-run-plans?")) return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue([]) });
      if (value.endsWith("/modules") && (!init || init.method === "GET")) return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue(modules) });
      if (value.endsWith("/modules/mod-a/revision-image-builds") && init?.method === "POST") return firstPost.promise;
      if (value.endsWith("/modules/mod-b/revision-image-builds") && init?.method === "POST") return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue(buildB) });
      if (value.endsWith("/revision-image-builds/build-b")) return secondStatus.promise;
      if (value.includes("/revision-image-builds/build-b/logs?")) return Promise.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ text: "late B output", total_bytes: 13, next_offset: null }) });
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderBundlesApp();

    const firstRow = (await screen.findByText("agent-a")).closest(".bundles-saved-row");
    const secondRow = screen.getByText("agent-b").closest(".bundles-saved-row");
    await userEvent.click(within(firstRow).getByRole("button", { name: "Build" }));
    expect(screen.getByRole("dialog", { name: "Image build · agent-a" })).toBeInTheDocument();
    await userEvent.click(within(secondRow).getByRole("button", { name: "Build" }));
    expect(await screen.findByRole("dialog", { name: "Image build · agent-b" })).toBeInTheDocument();

    await act(async () => {
      firstPost.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ id: "build-a", revision_id: "revision-a", generation: 1, status: "queued" }) });
      await Promise.resolve();
    });
    expect(screen.getByRole("dialog", { name: "Image build · agent-b" })).toBeInTheDocument();
    expect(screen.queryByText("Build build-a")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await act(async () => {
      secondStatus.resolve({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ ...buildB, status: "ready" }) });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("late B output")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
