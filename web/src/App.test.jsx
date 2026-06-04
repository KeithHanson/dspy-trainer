import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders shell routes without auth or settings", async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("/agent-run-plans?limit=50&offset=0")) {
        return Promise.resolve({ ok: true, json: vi.fn().mockResolvedValue([]) });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByText("Sign in to your workspace")).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
