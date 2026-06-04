import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { BundlesPage } from "./BundlesPage";

describe("BundlesPage", () => {
  it("shows github import panel when import query is present", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) })));
    render(
      <MemoryRouter initialEntries={["/bundles?import=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

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
            current_commit_sha: "abc12345",
            validation_status: "failed",
            diagnostics: [{ severity: "error", code: "module_missing", message: "module.py missing" }],
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/bundles?import=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("GitHub repository URL"), "https://github.com/example/repo-bundle");
    await userEvent.clear(screen.getByLabelText("Branch"));
    await userEvent.type(screen.getByLabelText("Branch"), "main");
    fireEvent.submit(screen.getByRole("button", { name: "Import + validate" }).closest("form"));

    await waitFor(() => expect(screen.getByText("Validation result")).toBeInTheDocument());
    expect(screen.getByText(/module_missing: module.py missing/)).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/github.com\/example\/repo-bundle/)).toBeInTheDocument();

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

    render(
      <MemoryRouter initialEntries={["/bundles?import=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

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

    render(
      <MemoryRouter initialEntries={["/bundles?import=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText(/GitHub access is not configured/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Import + validate" })).toBeDisabled();

    vi.unstubAllGlobals();
  });

  it("handles non-array diagnostics when viewing saved bundle", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-2",
              bundle_name: "support-triage-agent",
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

    render(
      <MemoryRouter>
        <BundlesPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "View files" }));
    expect(await screen.findByText("Bundle detail")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("updates bundle name and version from bundle edit modal", async () => {
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
      }
      if (String(url).endsWith("/modules") && (!init || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue([
            {
              id: "mod-2",
              bundle_name: "support-triage-agent",
              bundle_version: "0.1.0",
              validation_status: "passed",
              status: "imported",
              diagnostics: [],
            },
          ]),
        });
      }
      if (String(url).endsWith("/modules/mod-2/files")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ "module.py": "print('x')" }) });
      }
      if (String(url).endsWith("/modules/mod-2") && init?.method === "PATCH") {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            id: "mod-2",
            bundle_name: "renamed-bundle",
            bundle_version: "2.1.0",
            validation_status: "passed",
            status: "imported",
            diagnostics: [],
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <BundlesPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getByLabelText("Bundle name"));
    await userEvent.type(screen.getByLabelText("Bundle name"), "renamed-bundle");
    await userEvent.clear(screen.getByLabelText("Bundle version"));
    await userEvent.type(screen.getByLabelText("Bundle version"), "2.1.0");
    await userEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/modules\/mod-2$/), expect.objectContaining({ method: "PATCH" })));
    expect(await screen.findByText("renamed-bundle")).toBeInTheDocument();
    expect(await screen.findByText("v2.1.0")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("reloads bundle files when a file button is clicked", async () => {
    let fileFetchCount = 0;
    const fetchMock = vi.fn((url, init) => {
      if (String(url).endsWith("/ready")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ github: { configured: true } }) });
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

    render(
      <MemoryRouter>
        <BundlesPage />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "View files" }));
    expect(await screen.findByText("print(1)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "metric.py" }));

    await waitFor(() => expect(fileFetchCount).toBe(2));
    expect(await screen.findByText("metric_2")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
