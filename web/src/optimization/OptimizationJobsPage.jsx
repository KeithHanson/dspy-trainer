import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const JOBS_POLL_MS = 2500;

export function OptimizationJobsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const jobId = searchParams.get("job") || "";

  const [jobs, setJobs] = useState([]);
  const [job, setJob] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isRefreshingJob, setIsRefreshingJob] = useState(false);
  const [cancelingJobId, setCancelingJobId] = useState("");
  const [deletingJobId, setDeletingJobId] = useState("");
  const [error, setError] = useState("");
  const [moduleNames, setModuleNames] = useState({});
  const [moduleVersions, setModuleVersions] = useState({});
  const [profileNames, setProfileNames] = useState({});

  useEffect(() => {
    const loadModules = async () => {
      try {
        const response = await fetch(`${apiBase}/modules`, { method: "GET" });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        const next = {};
        const versions = {};
        (Array.isArray(payload) ? payload : []).forEach((item) => {
          if (item?.id) {
            next[item.id] = item.bundle_name || item.source_ref || item.id;
            versions[item.id] = item.bundle_version || "";
          }
        });
        setModuleNames(next);
        setModuleVersions(versions);
      } catch {
        setModuleNames({});
        setModuleVersions({});
      }
    };

    const loadProfiles = async () => {
      try {
        const response = await fetch(`${apiBase}/lm-profiles`, { method: "GET" });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        const next = {};
        (Array.isArray(payload) ? payload : []).forEach((profile) => {
          if (profile?.id) {
            next[profile.id] = profile.name || profile.id;
          }
        });
        setProfileNames(next);
      } catch {
        setProfileNames({});
      }
    };

    loadModules();
    loadProfiles();
  }, [apiBase]);

  const loadJobs = async () => {
    setIsLoading(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/optimization/jobs?limit=50&offset=0`, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Could not load optimization jobs (${response.status})`);
      }
      const payload = await response.json();
      setJobs(Array.isArray(payload) ? payload : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load optimization jobs");
      setJobs([]);
    } finally {
      setIsLoading(false);
    }
  };

  const loadJob = async (targetId, { background = false } = {}) => {
    if (background) {
      setIsRefreshingJob(true);
    } else {
      setIsLoading(true);
      setError("");
    }
    try {
      const response = await fetch(`${apiBase}/optimization/jobs/${encodeURIComponent(targetId)}`, { method: "GET" });
      if (!response.ok) {
        if (response.status === 404) {
          throw new Error("Optimization job not found");
        }
        throw new Error(`Could not load optimization job (${response.status})`);
      }
      const payload = await response.json();
      setJob(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load optimization job");
      if (!background) {
        setJob(null);
      }
    } finally {
      if (background) {
        setIsRefreshingJob(false);
      } else {
        setIsLoading(false);
      }
    }
  };

  const deleteJob = async (targetId) => {
    const confirmed = window.confirm("Delete this optimization job?");
    if (!confirmed) {
      return;
    }
    setDeletingJobId(targetId);
    setError("");
    try {
      const response = await fetch(`${apiBase}/optimization/jobs/${encodeURIComponent(targetId)}`, { method: "DELETE" });
      if (!response.ok) {
        throw new Error(`Could not delete optimization job (${response.status})`);
      }
      if (jobId === targetId) {
        navigate("/optimization/jobs");
        return;
      }
      setJobs((current) => current.filter((item) => item.id !== targetId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete optimization job");
    } finally {
      setDeletingJobId("");
    }
  };

  const cancelJob = async (targetId) => {
    const confirmed = window.confirm("Cancel this optimization job?");
    if (!confirmed) {
      return;
    }
    setCancelingJobId(targetId);
    setError("");
    try {
      const response = await fetch(`${apiBase}/optimization/jobs/${encodeURIComponent(targetId)}/cancel`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Could not cancel optimization job (${response.status})`);
      }
      const payload = await response.json();
      setJobs((current) => current.map((item) => (item.id === targetId ? payload : item)));
      if (jobId === targetId) {
        setJob(payload);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not cancel optimization job");
    } finally {
      setCancelingJobId("");
    }
  };

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      loadJobs();
      return;
    }
    loadJob(jobId);
  }, [apiBase, jobId]);

  useEffect(() => {
    if (!jobId || !job?.status || job.status === "succeeded" || job.status === "failed" || job.status === "canceled") {
      return undefined;
    }
    const interval = setInterval(() => {
      loadJob(jobId, { background: true });
    }, JOBS_POLL_MS);
    return () => clearInterval(interval);
  }, [apiBase, job?.status, jobId]);

  if (!jobId) {
    return (
      <section className="page">
        <div className="page-body optimization-wrap">
          <header className="row between plans-head">
            <div className="col gap-1">
              <h1 className="t-display" style={{ fontSize: 22 }}>Optimization Jobs</h1>
              <p className="muted t-sm">Monitor persisted optimization job executions and results.</p>
            </div>
            <Button onClick={() => navigate("/optimization")}>New optimization job</Button>
          </header>

          {isLoading ? <LoadingState label="Loading optimization jobs..." /> : null}
          {error ? <ErrorState title="Could not load optimization jobs" description={error} /> : null}

          {!isLoading && !error ? (
            jobs.length ? (
              <section className="panel optimization-jobs-table-wrap">
                <table className="dashboard-table">
                  <thead>
                    <tr>
                      <th>Job ID</th>
                      <th>Module</th>
                      <th>Strategy</th>
                      <th>Status</th>
                      <th>Delta</th>
                      <th>Started</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((item) => {
                      const moduleLabel = item?.module_import_id ? (moduleNames[item.module_import_id] || item.module_import_id) : "-";
                      const delta = item?.comparison_summary?.score_delta_pct;
                      const startedAt = formatDateTime(item?.run_started_at);
                      return (
                        <tr
                          key={item.id}
                          className="runs-row-click"
                          onClick={() => navigate(`/optimization/jobs?job=${encodeURIComponent(item.id)}`)}
                        >
                          <td className="mono">{shortId(item.id)}</td>
                          <td>{moduleLabel}</td>
                          <td>{item?.strategy || "-"}</td>
                          <td><StatusPill status={item?.status} /></td>
                          <td className={`mono ${toneForDelta(delta)}`}>{formatDelta(delta)}</td>
                          <td className="mono">{startedAt}</td>
                          <td>
                            {canCancelJob(item?.status) ? (
                              <Button
                                size="sm"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  cancelJob(item.id);
                                }}
                                disabled={cancelingJobId === item.id}
                              >
                                {cancelingJobId === item.id ? "Canceling..." : "Cancel"}
                              </Button>
                            ) : null}
                            <Button
                              size="sm"
                              variant="danger"
                              className={canCancelJob(item?.status) ? "optimization-job-action" : ""}
                              onClick={(event) => {
                                event.stopPropagation();
                                deleteJob(item.id);
                              }}
                              disabled={deletingJobId === item.id}
                            >
                              {deletingJobId === item.id ? "Deleting..." : "Delete"}
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </section>
            ) : (
              <EmptyState title="No optimization jobs yet" description="Launch an optimization job first to see entries here." />
            )
          ) : null}
        </div>
      </section>
    );
  }

  const comparison = job?.comparison_summary || {};
  const detailJobId = job?.id || "";

  const executionLm = job?.execution_lm_profile_id ? (profileNames[job.execution_lm_profile_id] || job.execution_lm_profile_id) : "-";
  const helperLm = job?.helper_lm_profile_id ? (profileNames[job.helper_lm_profile_id] || job.helper_lm_profile_id) : "-";
  const generatedBundleName = job?.generated_module_import_id ? (moduleNames[job.generated_module_import_id] || job.generated_module_import_id) : "-";

  return (
    <section className="page">
      <div className="page-body optimization-wrap">
        <header className="row between plans-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Optimization Job Detail</h1>
            <p className="muted t-sm mono">{detailJobId || "-"}</p>
          </div>
          <div className="row gap-2 optimization-detail-actions">
            {detailJobId && canCancelJob(job?.status) ? (
              <Button onClick={() => cancelJob(detailJobId)} disabled={cancelingJobId === detailJobId}>
                {cancelingJobId === detailJobId ? "Canceling..." : "Cancel job"}
              </Button>
            ) : null}
            {detailJobId ? (
              <Button variant="danger" onClick={() => deleteJob(detailJobId)} disabled={deletingJobId === detailJobId}>
                {deletingJobId === detailJobId ? "Deleting..." : "Delete job"}
              </Button>
            ) : null}
          </div>
        </header>

        {isLoading ? <LoadingState label="Loading optimization job..." /> : null}
        {error ? <ErrorState title="Could not load optimization job" description={error} /> : null}

        {job ? (
          <div className="optimization-detail-layout col">
            {job.status === "failed" ? (
              <ErrorState
                title="Optimization job failed"
                description={job.failure_reason || "The optimization job failed without a reported reason."}
              />
            ) : null}

            <section className="col optimization-detail-section">
              <div className="runs-kpis optimization-summary-kpis">
                <Kpi label="Run Status" value={<StatusPill status={job.status} />} />
                <Kpi label="Strategy" value={job.strategy || "-"} valueClassName="optimization-strategy-kpi-value" />
                <Kpi label="Total Runtime" value={formatDuration(job.created_at, job.finished_at)} />
                <Kpi label="Baseline score" value={formatPercent(comparison?.baseline_score_pct)} valueClassName="optimization-comparison-baseline" />
                <Kpi label="Optimized score" value={formatPercent(comparison?.optimized_score_pct)} valueClassName={getOptimizedComparisonClass(comparison?.optimized_score_pct, comparison?.baseline_score_pct)} />
                <Kpi label="Delta" value={formatDelta(comparison?.score_delta_pct)} valueClassName={getDeltaComparisonClass(comparison?.score_delta_pct)} />
              </div>

              {job.status !== "succeeded" && job.status !== "failed" && job.status !== "canceled" ? (
                <div className="optimization-live-note" role="status">
                  {isRefreshingJob ? "Refreshing live run output..." : "Live run output refreshes automatically."}
                </div>
              ) : null}
            </section>

            <section className="col optimization-detail-section optimization-form-block">
              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Run configuration</h3>
                <div className="optimization-config-grid">
                  <div className="optimization-config-card">
                    <span className="t-label">Execution LM</span>
                    <strong className="optimization-config-value">{executionLm}</strong>
                  </div>
                  <div className="optimization-config-card">
                    <span className="t-label">Helper LM</span>
                    <strong className="optimization-config-value">{helperLm}</strong>
                  </div>
                  <div className="optimization-config-card">
                    <span className="t-label">Source run plan</span>
                    {job.source_run_plan_id ? (
                      <Link className="lnk optimization-config-link" to={`/runs?plan=${encodeURIComponent(job.source_run_plan_id)}`}>
                        {job.source_run_plan_id}
                      </Link>
                    ) : (
                      <strong className="optimization-config-value">-</strong>
                    )}
                  </div>
                </div>
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Generated outputs</h3>
                <div className="optimization-config-grid">
                  <div className="optimization-config-card">
                    <span className="t-label">Generated bundle</span>
                    {job.generated_module_import_id ? (
                      <Link className="lnk optimization-config-link" to="/bundles">{generatedBundleName}</Link>
                    ) : (
                      <strong className="optimization-config-value">Pending</strong>
                    )}
                  </div>
                  <div className="optimization-config-card">
                    <span className="t-label">Evaluation plan</span>
                    {job.optimized_evaluation_plan_id ? (
                      <Link className="lnk optimization-config-link" to={`/plans?new=1&id=${encodeURIComponent(job.optimized_evaluation_plan_id)}`}>
                        {job.optimized_evaluation_plan_id}
                      </Link>
                    ) : (
                      <strong className="optimization-config-value">Pending</strong>
                    )}
                  </div>
                  <div className="optimization-config-card">
                    <span className="t-label">Optimized eval run</span>
                    {job.optimized_eval_run_plan_id ? (
                      <Link className="lnk optimization-config-link" to={`/runs?plan=${encodeURIComponent(job.optimized_eval_run_plan_id)}`}>
                        {job.optimized_eval_run_plan_id}
                      </Link>
                    ) : (
                      <strong className="optimization-config-value">Pending</strong>
                    )}
                  </div>
                </div>
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Process log</h3>
                <pre className="optimization-request-config-preview"><code>{job.execution_log || "No execution log captured yet."}</code></pre>
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function StatusPill({ status }) {
  const normalized = String(status || "").toLowerCase();
  const toneClass = normalized === "succeeded"
    ? "runs-status-pill-pass"
    : normalized === "failed"
      ? "runs-status-pill-fail"
      : normalized === "running"
        ? "runs-status-pill-run"
        : "runs-status-pill-neutral";
  return <span className={`plans-status ${toneClass}`}>{status || "unknown"}</span>;
}

function Kpi({ label, value, valueClassName = "" }) {
  return (
    <div className="runs-kpi">
      <div className="t-label">{label}</div>
      <div className={["runs-kpi-value", valueClassName].filter(Boolean).join(" ")}>{value}</div>
    </div>
  );
}

function shortId(value) {
  return typeof value === "string" ? value.slice(0, 10) : "-";
}

function formatDateTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(startValue, endValue) {
  const start = new Date(startValue);
  const end = new Date(endValue);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end.getTime() < start.getTime()) {
    return "-";
  }
  const totalSeconds = Math.round((end.getTime() - start.getTime()) / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }
  const totalMinutes = Math.round(totalSeconds / 60);
  if (totalMinutes < 60) {
    return `${totalMinutes}m`;
  }
  const totalHours = (totalMinutes / 60).toFixed(1).replace(/\.0$/, "");
  return `${totalHours}h`;
}

function formatPercent(value) {
  if (!Number.isFinite(Number(value))) {
    return "-";
  }
  return `${Number(value).toFixed(1)}%`;
}

function formatDelta(value) {
  if (!Number.isFinite(Number(value))) {
    return "-";
  }
  const numeric = Number(value);
  const sign = numeric > 0 ? "+" : "";
  return `${sign}${numeric.toFixed(1)}%`;
}

function formatDeltaLabel(value) {
  if (!Number.isFinite(Number(value))) {
    return "Delta: pending";
  }
  const numeric = Number(value);
  return numeric >= 0 ? `Delta +${numeric.toFixed(1)}%` : `Delta ${numeric.toFixed(1)}%`;
}

function toneForDelta(value) {
  if (!Number.isFinite(Number(value))) {
    return "";
  }
  const numeric = Number(value);
  if (numeric > 0) {
    return "runs-kpi-pass";
  }
  if (numeric < 0) {
    return "runs-kpi-fail";
  }
  return "runs-kpi-warn";
}

function getOptimizedComparisonClass(optimizedValue, baselineValue) {
  const optimized = Number(optimizedValue);
  const baseline = Number(baselineValue);
  if (!Number.isFinite(optimized) || !Number.isFinite(baseline)) {
    return "";
  }
  if (optimized > baseline) {
    return "runs-kpi-pass";
  }
  if (optimized < baseline) {
    return "runs-kpi-fail";
  }
  return "runs-kpi-warn";
}

function getDeltaComparisonClass(deltaValue) {
  const delta = Number(deltaValue);
  if (!Number.isFinite(delta)) {
    return "";
  }
  if (delta > 0) {
    return "runs-kpi-pass";
  }
  if (delta < 0) {
    return "runs-kpi-fail";
  }
  return "runs-kpi-warn";
}

function canCancelJob(status) {
  return status === "queued" || status === "running";
}
