import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  function stubActivityFetch(plans, jobs = []) {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("/agent-run-plans?limit=50&offset=0")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue(plans),
        });
      }
      if (String(url).includes("/optimization/jobs?limit=50&offset=0")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue(jobs),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("highlights active nav item", () => {
    stubActivityFetch([]);
    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const runsLink = screen.getByRole("link", { name: "Eval Runs" });
    expect(runsLink).toHaveClass("shell-nav-item-active");
    vi.unstubAllGlobals();
  });

  it("renders datasets above evaluation plans in nav", () => {
    stubActivityFetch([]);
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const links = screen.getAllByRole("link");
    const datasetsIndex = links.findIndex((link) => link.textContent?.includes("Datasets"));
    const plansIndex = links.findIndex((link) => link.textContent?.includes("Evaluation Plans"));
    expect(datasetsIndex).toBeGreaterThan(-1);
    expect(plansIndex).toBeGreaterThan(-1);
    expect(datasetsIndex).toBeLessThan(plansIndex);
    vi.unstubAllGlobals();
  });

  it("highlights optimization nav item", () => {
    stubActivityFetch([]);
    render(
      <MemoryRouter initialEntries={["/optimization"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const optimizationLink = screen.getByRole("link", { name: "Optimization" });
    expect(optimizationLink).toHaveClass("shell-nav-item-active");
    expect(screen.getByRole("link", { name: "Optimization Jobs" })).not.toHaveClass("shell-nav-item-active");
    vi.unstubAllGlobals();
  });

  it("highlights optimization jobs nav item", () => {
    stubActivityFetch([]);
    render(
      <MemoryRouter initialEntries={["/optimization/jobs"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const jobsLink = screen.getByRole("link", { name: "Optimization Jobs" });
    expect(jobsLink).toHaveClass("shell-nav-item-active");
    expect(screen.getByRole("link", { name: "Optimization" })).not.toHaveClass("shell-nav-item-active");
    vi.unstubAllGlobals();
  });

  it("highlights endpoints nav item on nested editor routes", () => {
    stubActivityFetch([]);
    render(
      <MemoryRouter initialEntries={["/endpoints/new"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const endpointsLink = screen.getByRole("link", { name: "Endpoints" });
    expect(endpointsLink).toHaveClass("shell-nav-item-active");
    vi.unstubAllGlobals();
  });

  it("renders external utility links", () => {
    stubActivityFetch([]);
    render(
      <MemoryRouter initialEntries={["/plans"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "MLFlow" })).toHaveAttribute("href", "http://localhost:5001");
    expect(screen.getByRole("link", { name: "LiteLLM Proxy" })).toHaveAttribute("href", "http://localhost:4000");
    expect(screen.queryByText("Operator")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("shows runs live dot only when a run is active", async () => {
    stubActivityFetch([{ id: "plan-1", status: "running", running_tasks: 1 }]);
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Eval Runs" })).toHaveTextContent("Eval Runs");
      expect(document.querySelector(".d-live")).not.toBeNull();
    });
    vi.unstubAllGlobals();
  });

  it("hides runs live dot when no run is active", async () => {
    stubActivityFetch([{ id: "plan-1", status: "succeeded", running_tasks: 0 }]);
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Eval Runs" })).toHaveTextContent("Eval Runs");
    });
    expect(document.querySelector(".d-live")).toBeNull();
    vi.unstubAllGlobals();
  });

  it("shows optimization live dot when an optimization job is active", async () => {
    stubActivityFetch([], [{ id: "opt-1", status: "running" }]);
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Optimization Jobs" })).toHaveTextContent("Optimization Jobs");
      expect(document.querySelectorAll(".d-live").length).toBeGreaterThan(0);
    });
    vi.unstubAllGlobals();
  });
});
