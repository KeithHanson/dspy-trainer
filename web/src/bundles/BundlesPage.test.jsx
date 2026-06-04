import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { BundlesPage } from "./BundlesPage";

describe("BundlesPage", () => {
  it("downloads sample bundle when action is clicked", async () => {
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

    render(
      <MemoryRouter>
        <BundlesPage />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Example bundle" }));

    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/samples\/module-bundle$/), { method: "GET" });
    expect(urlMock).toHaveBeenCalledTimes(1);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeMock).toHaveBeenCalledWith("blob:test");

    vi.unstubAllGlobals();
    urlMock.mockRestore();
    revokeMock.mockRestore();
    anchorClick.mockRestore();
  });

  it("shows upload and validate panel when upload query is present", () => {
    render(
      <MemoryRouter initialEntries={["/bundles?upload=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Step 2: Upload and validate bundle")).toBeInTheDocument();
  });

  it("submits import + validate flow and renders diagnostics", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).endsWith("/modules/import")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue({ id: "mod-1" }) });
      }
      if (String(url).endsWith("/modules/mod-1/validate-upload")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue({
            validation_status: "failed",
            diagnostics: [{ level: "error", code: "module_missing", message: "module.py missing" }],
          }),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/bundles?upload=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

    const file = new File(["zip"], "bundle.zip", { type: "application/zip" });
    const fileInput = screen.getByLabelText("Bundle zip");
    fireEvent.change(fileInput, { target: { files: [file] } });
    fireEvent.submit(screen.getByRole("button", { name: "Upload + validate" }).closest("form"));

    await waitFor(() => expect(screen.getByText("Validation result")).toBeInTheDocument());
    expect(screen.getByText(/module_missing: module.py missing/)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("handles non-array diagnostics when viewing saved bundle", async () => {
    const fetchMock = vi.fn((url) => {
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
      if (String(url).endsWith("/samples/module-bundle")) {
        return Promise.resolve({ ok: true, blob: vi.fn().mockResolvedValue(new Blob(["zip"])) });
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
    expect(await screen.findByText("Bundle metadata")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("updates bundle name and version from bundle detail", async () => {
    const fetchMock = vi.fn((url, init) => {
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
      if (String(url).endsWith("/samples/module-bundle")) {
        return Promise.resolve({ ok: true, blob: vi.fn().mockResolvedValue(new Blob(["zip"])) });
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
    await userEvent.clear(screen.getByLabelText("Bundle name"));
    await userEvent.type(screen.getByLabelText("Bundle name"), "renamed-bundle");
    await userEvent.clear(screen.getByLabelText("Bundle version"));
    await userEvent.type(screen.getByLabelText("Bundle version"), "2.1.0");
    await userEvent.click(screen.getByRole("button", { name: "Save metadata" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/modules\/mod-2$/), expect.objectContaining({ method: "PATCH" })));
    expect(await screen.findByDisplayValue("renamed-bundle")).toBeInTheDocument();
    expect(await screen.findByDisplayValue("2.1.0")).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
