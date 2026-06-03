import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  function stubPlansFetch(plans) {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes("/agent-run-plans?limit=50&offset=0")) {
        return Promise.resolve({
          ok: true,
          json: vi.fn().mockResolvedValue(plans),
        });
      }
      return Promise.reject(new Error(`Unexpected URL ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("highlights active nav item", () => {
    stubPlansFetch([]);
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

  it("highlights optimization nav item", () => {
    stubPlansFetch([]);
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
    stubPlansFetch([]);
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

  it("renders breadcrumb trail for route", () => {
    stubPlansFetch([]);
    render(
      <MemoryRouter initialEntries={["/plans"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const breadcrumbNav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumbNav).toBeInTheDocument();
    expect(breadcrumbNav).toHaveTextContent("Default");
    expect(breadcrumbNav).toHaveTextContent("Evaluation Plans");
    vi.unstubAllGlobals();
  });

  it("renders optimization breadcrumb trail", async () => {
    stubPlansFetch([]);
    render(
      <MemoryRouter initialEntries={["/optimization"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const breadcrumbNav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumbNav).toHaveTextContent("Default");
    expect(breadcrumbNav).toHaveTextContent("Optimization");
    vi.unstubAllGlobals();
  });

  it("renders optimization jobs breadcrumb trail", async () => {
    stubPlansFetch([]);
    render(
      <MemoryRouter initialEntries={["/optimization/jobs"]}>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>,
    );

    const breadcrumbNav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumbNav).toHaveTextContent("Default");
    expect(breadcrumbNav).toHaveTextContent("Optimization Jobs");
    vi.unstubAllGlobals();
  });

  it("shows runs live dot only when a run is active", async () => {
    stubPlansFetch([{ id: "plan-1", status: "running", running_tasks: 1 }]);
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
    stubPlansFetch([{ id: "plan-1", status: "succeeded", running_tasks: 0 }]);
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
});
