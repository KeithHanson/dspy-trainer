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
  const liveJobRow = activeJobs[0] || null;
  const failedJobs = planRows.filter((plan) => plan.status === "failed");
  const validatedBundles = moduleRows.filter((item) => item.validation_status === "passed").length;
  const failedBundles = moduleRows.filter((item) => item.validation_status === "failed");
  const onlineWorkers = workerRows.filter((worker) => worker.status === "running" || worker.status === "listening").length;

  const totalEvalPass = planRows.reduce((sum, row) => sum + Number(row.eval_pass_count || 0), 0);
  const totalEvalFail = planRows.reduce((sum, row) => sum + Number(row.eval_fail_count || 0), 0);
  const passRate = pct(totalEvalPass, totalEvalPass + totalEvalFail);

  const avgScores = planRows.map((row) => row.average_score).filter((value) => typeof value === "number");
  const meanScore = avgScores.length ? avgScores.reduce((sum, value) => sum + value, 0) / avgScores.length : null;

  const mostRecent = planRows[0];
  const recentLabel = mostRecent?.created_at ? `last run ${formatTimeAgo(mostRecent.created_at)}` : "no runs yet";

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

  const liveJob = liveJobRow
    ? {
        id: String(liveJobRow.id),
        planName: liveJobRow.plan_name || "RunPlan",
        bundleName: moduleNameById.get(String(liveJobRow.module_import_id || "")) || liveJobRow.bundle_path || "bundle",
        progressPct: Math.round(
          pct(
            Number(liveJobRow.completed_tasks || 0) + Number(liveJobRow.failed_tasks || 0),
            Number(liveJobRow.total_tasks || 0),
          ) * 100,
        ),
        passRate: pct(
          Number(liveJobRow.completed_tasks || 0),
          Number(liveJobRow.completed_tasks || 0) + Number(liveJobRow.failed_tasks || 0),
        ),
      }
    : null;

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
      title: `${failedJobs.length} eval job${failedJobs.length === 1 ? "" : "s"} failed`,
      detail: "Review errored runs to inspect worker logs and failures.",
      ctaLabel: "Open runs",
      ctaTo: "/runs",
    });
  }

  return {
    greetingName: "there",
    summaryLine: `${activeJobs.length} active jobs · ${validatedBundles} validated bundles · ${recentLabel}`,
    liveJob,
    kpis: [
      { id: "pass", label: "Pass rate", value: formatPercent(passRate), delta: `${totalEvalPass}/${totalEvalPass + totalEvalFail}` },
      { id: "jobs", label: "Active eval jobs", value: String(activeJobs.length), delta: `${failedJobs.length} failed` },
      { id: "bundles", label: "Validated bundles", value: String(validatedBundles), delta: `${moduleRows.length} total` },
      { id: "score", label: "Average score", value: formatScore(meanScore), delta: `${onlineWorkers} workers online` },
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
