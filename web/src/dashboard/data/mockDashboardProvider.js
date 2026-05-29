export const mockDashboardProvider = {
  async getOverview() {
    return {
      greetingName: "Kira",
      summaryLine: "3 plans active · 8 validated bundles · last run 2m ago",
      liveJob: {
        id: "run-104",
        planName: "Support triage baseline",
        bundleName: "sql-generator v7",
        progressPct: 58,
        passRate: 0.81,
        stats: { pass: 77, fail: 18, running: 9, queued: 16, done: 95, total: 120 },
      },
      kpis: [
        { id: "pass", label: "Pass rate · 7d", value: "82.4%", delta: "+3.1pt", tone: "pass" },
        { id: "jobs", label: "Eval jobs · 7d", value: "41", delta: "+12", tone: "run" },
        { id: "tasks", label: "Tasks judged", value: "6,284", delta: "+1.4k", tone: "info" },
        { id: "latency", label: "Avg item latency", value: "2.8s", delta: "-0.4s", tone: "warn" },
      ],
      recentJobs: [
        { id: "run-104", planName: "Support triage baseline", bundleName: "sql-generator v7", status: "running", progress: { done: 95, total: 120 }, passRate: 0.81, startedLabel: "2m ago" },
        { id: "run-103", planName: "Refund edge-case checks", bundleName: "policy-bot v4", status: "complete", progress: { done: 120, total: 120 }, passRate: 0.88, startedLabel: "48m ago" },
        { id: "run-102", planName: "Escalation calibration", bundleName: "routing-agent v2", status: "failed", progress: { done: 37, total: 120 }, passRate: 0.41, startedLabel: "3h ago" },
      ],
      alerts: [
        {
          id: "alert-bundle-validation",
          severity: "fail",
          title: "sql-generator v7 failed validation",
          detail: "metric.py is missing the trace parameter and dspy pin conflicts.",
          ctaLabel: "Fix",
          ctaTo: "/bundles?bundle=sql-generator-v7",
        },
      ],
      quickStart: [
        { id: "upload", title: "Upload your module", detail: "module.py + metric.py zipped", to: "/bundles?upload=1" },
        { id: "plan", title: "Create a new plan", detail: "Define question set and runs", to: "/plans?new=1" },
        { id: "team", title: "Invite your team", detail: "Share results with reviewers", to: "/team" },
      ],
    };
  },
};
