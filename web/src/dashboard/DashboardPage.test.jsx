import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { DashboardPage } from "./DashboardPage";

function makeAdapter(data) {
  return {
    getOverview: vi.fn().mockResolvedValue(data),
  };
}

const baseOverview = {
  greetingName: "Kira",
  summaryLine: "Summary",
  liveJob: null,
  spotlightJob: null,
  kpis: [{ id: "one", label: "Pass rate", value: "80%", delta: "+1.0" }],
  recentJobs: [],
  alerts: [],
  quickStart: [{ id: "q1", title: "Create plan", detail: "Start quickly", to: "/plans?new=1" }],
};

describe("DashboardPage", () => {
  it("shows live strip only when live job exists", async () => {
    const withLive = makeAdapter({
      ...baseOverview,
      liveJob: { id: "run-5", planName: "Live plan", bundleName: "bundle", progressPct: 40, passCount: 4, failCount: 1, errorCount: 0, doneCount: 5 },
      spotlightJob: { id: "run-5", planName: "Live plan", bundleName: "bundle", progressPct: 40, passCount: 4, failCount: 1, errorCount: 0, doneCount: 5 },
    });

    const noLive = makeAdapter(baseOverview);

    const { rerender } = render(
      <MemoryRouter>
        <DashboardPage adapter={withLive} />
      </MemoryRouter>,
    );

  expect(await screen.findByText("Live run: Live plan")).toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <DashboardPage adapter={noLive} />
      </MemoryRouter>,
    );

  expect(await screen.findByText("No runs yet.")).toBeInTheDocument();
  });

  it("renders recent jobs empty state", async () => {
    render(
      <MemoryRouter>
        <DashboardPage adapter={makeAdapter(baseOverview)} />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No recent jobs")).toBeInTheDocument();
  });

  it("calls row handler when recent job row is clicked", async () => {
    const onOpenRun = vi.fn();
    const adapter = makeAdapter({
      ...baseOverview,
      recentJobs: [
        {
          id: "run-9",
          planName: "Refund checks",
          bundleName: "policy-bot v4",
          status: "running",
          progress: { done: 2, total: 10 },
          passRate: 0.5,
          startedLabel: "1m ago",
        },
      ],
    });

    render(
      <MemoryRouter>
        <DashboardPage adapter={adapter} onOpenRun={onOpenRun} />
      </MemoryRouter>,
    );

    await userEvent.click(await screen.findByText("Refund checks"));
    expect(onOpenRun).toHaveBeenCalledWith("run-9");
  });
});
