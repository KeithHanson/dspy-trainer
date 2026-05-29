import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { BundlesPage } from "./BundlesPage";

describe("BundlesPage", () => {
  it("downloads sample bundle when action is clicked", async () => {
    const blob = new Blob(["zip"], { type: "application/zip" });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: vi.fn().mockResolvedValue(blob) });
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

    await userEvent.click(screen.getByRole("button", { name: "Download sample bundle" }));

    expect(fetchMock).toHaveBeenCalledWith("/samples/module-bundle", { method: "GET" });
    expect(urlMock).toHaveBeenCalledTimes(1);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeMock).toHaveBeenCalledWith("blob:test");

    vi.unstubAllGlobals();
    urlMock.mockRestore();
    revokeMock.mockRestore();
    anchorClick.mockRestore();
  });

  it("shows upload step guidance when upload query is present", () => {
    render(
      <MemoryRouter initialEntries={["/bundles?upload=1"]}>
        <BundlesPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Upload flow is next in sequence")).toBeInTheDocument();
  });
});
