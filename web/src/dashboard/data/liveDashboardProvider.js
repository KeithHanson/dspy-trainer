function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function formatTimeAgo(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "unknown";
  }
  const deltaMs = Date.now() - parsed.getTime();
  const mins = Math.max(0, Math.floor(deltaMs / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function pct(numerator, denominator) {
  if (!denominator) {
    return 0;
  }
  return Math.max(0, Math.min(1, numerator / denominator));
}

function formatPercent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function formatScore(value) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

function buildEvalBreakdown(row, moduleNameById) {
  if (!row) return null;
  const passCount = Number(row.eval_pass_count || 0);
  const failCount = Number(row.eval_fail_count || 0);
  const totalDone = Number(row.completed_tasks || 0) + Number(row.failed_tasks || 0);
  const errorCount = Math.max(0, totalDone - passCount - failCount);
  const totalCount = Number(row.total_tasks || 0);
  return {
    id: String(row.id),
    planName: row.plan_name || "RunPlan",
    bundleName: moduleNameById.get(String(row.module_import_id || "")) || row.bundle_path || "bundle",
    status: row.status || "queued",
    progressPct: Math.round(pct(totalDone, Number(row.total_tasks || 0)) * 100),
    passCount,
    failCount,
    errorCount,
    doneCount: totalDone,
    totalCount,
  };
}

export function mapDashboardOverview({ plans, modules, workers }) {
  const planRows = toArray(plans);
  const moduleRows = toArray(modules);
  const workerRows = toArray(workers);
  const moduleNameById = new Map(
    moduleRows.map((item) => {
      const version = item.bundle_version ? ` v${item.bundle_version}` : "";
      const name = item.bundle_name ? `${item.bundle_name}${version}` : item.id;
      return [String(item.id), name];
    }),
  );

  const activeJobs = planRows.filter((plan) => plan.status === "running" || plan.status === "queued");
  const pendingEvalCount = planRows
    .filter((plan) => plan.status === "queued" || plan.status === "running")
    .reduce((sum, plan) => {
      const total = Number(plan.total_tasks || 0);
      const done = Number(plan.completed_tasks || 0) + Number(plan.failed_tasks || 0);
      return sum + Math.max(0, total - done);
    }, 0);
  const liveJobRow = activeJobs[0] || null;
  const failedJobs = planRows.filter((plan) => plan.status === "failed");
  const validatedBundles = moduleRows.filter((item) => item.validation_status === "passed").length;
  const failedBundles = moduleRows.filter((item) => item.validation_status === "failed");
  const availableWorkers = workerRows.filter((worker) => worker.status === "listening").length;
  const totalWorkers = workerRows.filter((worker) => worker.status === "listening" || worker.status === "running").length;

  const mostRecent = planRows[0];
  const recentLabel = mostRecent?.created_at ? `last run ${formatTimeAgo(mostRecent.created_at)}` : "no runs yet";
  const recentPassCount = Number(mostRecent?.eval_pass_count || 0);
  const recentFailCount = Number(mostRecent?.eval_fail_count || 0);
  const recentTotalJudged = recentPassCount + recentFailCount;
  const recentPassRate = pct(recentPassCount, recentTotalJudged);
  const recentAverageScore = typeof mostRecent?.average_score === "number" ? mostRecent.average_score : null;

  const recentJobs = planRows.slice(0, 8).map((row) => {
    const done = Number(row.completed_tasks || 0) + Number(row.failed_tasks || 0);
    const total = Number(row.total_tasks || 0);
    return {
      id: String(row.id),
      planName: row.plan_name || "RunPlan",
      bundleName: moduleNameById.get(String(row.module_import_id || "")) || row.bundle_path || "bundle",
      status: row.status || "queued",
      progress: { done, total },
      passRate: pct(Number(row.completed_tasks || 0), done || total),
      startedLabel: row.created_at ? formatTimeAgo(row.created_at) : "unknown",
    };
  });

  const liveJob = buildEvalBreakdown(liveJobRow, moduleNameById);
  const spotlightJob = buildEvalBreakdown(liveJobRow || mostRecent, moduleNameById);

  const alerts = [
    ...failedBundles.slice(0, 2).map((bundle) => ({
      id: `bundle-${bundle.id}`,
      title: `${bundle.bundle_name || bundle.id} failed validation`,
      detail: "Open bundle details to inspect diagnostics.",
      ctaLabel: "Open bundle",
      ctaTo: "/bundles",
    })),
  ];
  if (failedJobs.length > 0) {
    alerts.push({
      id: "failed-runs",
      title: `${failedJobs.length} run${failedJobs.length === 1 ? "" : "s"} failed`,
      detail: "Review errored runs to inspect worker logs and failures.",
      ctaLabel: "Open runs",
      ctaTo: "/runs",
    });
  }

  return {
    greetingName: "there",
    summaryLine: `${activeJobs.length} active jobs · ${validatedBundles} validated bundles · ${recentLabel}`,
    liveJob,
    spotlightJob,
    kpis: [
      { id: "pass", label: "Recent pass rate", value: formatPercent(recentPassRate), delta: `${recentPassCount}/${recentTotalJudged}` },
      { id: "score", label: "Recent average score", value: formatScore(recentAverageScore), delta: "" },
      { id: "jobs", label: "Pending evals", value: String(pendingEvalCount), delta: "" },
      { id: "bundles", label: "Validated bundles", value: String(validatedBundles), delta: "" },
      { id: "workers", label: "Available workers", value: `${availableWorkers}/${totalWorkers}`, delta: "" },
    ],
    recentJobs,
    alerts,
    quickStart: [
      { id: "upload", title: "Upload your module", detail: "module.py + metric.py zipped", to: "/bundles?upload=1" },
      { id: "plan", title: "Create a new plan", detail: "Define question set and runs", to: "/plans?new=1" },
      { id: "runs", title: "Review run monitor", detail: "Inspect scores, pass/fail, and traces", to: "/runs" },
    ],
  };
}

export function createLiveDashboardProvider(apiBase) {
  const normalizedApiBase = (apiBase || "http://localhost:8000").replace(/\/$/, "");

  return {
    async getOverview() {
      const [plansResp, modulesResp, workersResp] = await Promise.all([
        fetch(`${normalizedApiBase}/agent-run-plans?limit=50&offset=0`, { method: "GET" }),
        fetch(`${normalizedApiBase}/modules`, { method: "GET" }),
        fetch(`${normalizedApiBase}/workers`, { method: "GET" }),
      ]);

      if (!plansResp.ok) {
        throw new Error(`Could not load run plans (${plansResp.status})`);
      }
      if (!modulesResp.ok) {
        throw new Error(`Could not load modules (${modulesResp.status})`);
      }
      if (!workersResp.ok) {
        throw new Error(`Could not load workers (${workersResp.status})`);
      }

      const [plans, modules, workers] = await Promise.all([plansResp.json(), modulesResp.json(), workersResp.json()]);
      return mapDashboardOverview({ plans, modules, workers });
    },
  };
}
