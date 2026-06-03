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
  const [deletingJobId, setDeletingJobId] = useState("");
  const [error, setError] = useState("");
  const [moduleNames, setModuleNames] = useState({});
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
        (Array.isArray(payload) ? payload : []).forEach((item) => {
          if (item?.id) {
            next[item.id] = item.bundle_name || item.source_ref || item.id;
          }
        });
        setModuleNames(next);
      } catch {
        setModuleNames({});
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
                            <Button
                              size="sm"
                              variant="danger"
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

  const startedAt = formatDateTime(job?.run_started_at);
  const finishedAt = formatDateTime(job?.finished_at);
  const comparison = job?.comparison_summary || {};
  const telemetrySummary = job?.telemetry_summary || {};
  const artifactMetadata = job?.artifact_metadata || {};
  const strategyDetails = telemetrySummary.strategy_details || {};
  const detailJobId = job?.id || "";

  const executionLm = job?.execution_lm_profile_id ? (profileNames[job.execution_lm_profile_id] || job.execution_lm_profile_id) : "-";
  const helperLm = job?.helper_lm_profile_id ? (profileNames[job.helper_lm_profile_id] || job.helper_lm_profile_id) : "-";

  return (
    <section className="page">
      <div className="page-body optimization-wrap">
        <header className="row between plans-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Optimization Job Detail</h1>
            <p className="muted t-sm">Deep view of one persisted optimization run and its outcomes.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={() => navigate("/optimization/jobs")}>All optimization jobs</Button>
            <Button onClick={() => navigate("/optimization")}>Back to launch</Button>
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
              <h2 className="t-h2">Run summary</h2>
              <div className="optimization-detail-facts col">
                <p className="cap mono">Job ID: {job.id}</p>
                <p className="cap mono">Module: {job.module_import_id ? (moduleNames[job.module_import_id] || job.module_import_id) : "-"}</p>
              </div>

              <div className="runs-kpis">
                <Kpi label="Status" value={<StatusPill status={job.status} />} />
                <Kpi label="Artifact type" value={artifactMetadata?.artifact_type || "pending"} />
                <Kpi label="Baseline score" value={formatPercent(comparison?.baseline_score_pct)} />
                <Kpi label="Optimized score" value={formatPercent(comparison?.optimized_score_pct)} />
              </div>

              {job.status !== "succeeded" && job.status !== "failed" && job.status !== "canceled" ? (
                <div className="optimization-live-note" role="status">
                  {isRefreshingJob ? "Refreshing live run output..." : "Live run output refreshes automatically."}
                </div>
              ) : null}

              <div className="panel card-pad optimization-detail-panel">
                <div className="row between" style={{ marginBottom: 10 }}>
                  <h3 className="t-h2">Comparison</h3>
                  <span className="t-label">{formatDeltaLabel(comparison?.score_delta_pct)}</span>
                </div>
                <div className="optimization-grid-2">
                  <div>
                    <div className="t-label">Baseline / Optimized / Delta</div>
                    <p className="runs-kpi-value">{`${formatPercent(comparison?.baseline_score_pct)} / ${formatPercent(comparison?.optimized_score_pct)} / ${formatDelta(comparison?.score_delta_pct)}`}</p>
                  </div>
                  <div>
                    <div className="t-label">Artifact</div>
                    <p className="mono">{job.artifact_path || "Pending"}</p>
                  </div>
                </div>
              </div>
            </section>

            <section className="col optimization-detail-section optimization-form-block">
              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Run configuration</h3>
                <div className="optimization-detail-facts col">
                  <p className="cap mono">Strategy: {job.strategy || "-"}</p>
                  <p className="cap mono">Objective: {job.objective || "-"}</p>
                  <p className="cap mono">Execution LM: {executionLm}</p>
                  <p className="cap mono">Helper LM: {helperLm}</p>
                  <p className="cap mono">Training dataset: {job.dataset_id || "not set"}</p>
                  <p className="cap mono">Validation dataset: {job.validation_dataset_id || "not set"}</p>
                </div>
                {job.source_run_plan_id ? (
                  <p className="cap">
                    Source run plan:
                    <Link className="lnk" to={`/runs?plan=${encodeURIComponent(job.source_run_plan_id)}`}>
                      {job.source_run_plan_id}
                    </Link>
                  </p>
                ) : null}
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Timing</h3>
                <div className="optimization-detail-facts col">
                  <p className="cap mono">Created: {formatDateTime(job.created_at)}</p>
                  <p className="cap mono">Started: {startedAt}</p>
                  <p className="cap mono">Finished: {finishedAt}</p>
                </div>
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Compiled artifact metadata</h3>
                <pre className="optimization-request-config-preview"><code>{JSON.stringify(artifactMetadata, null, 2)}</code></pre>
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Strategy details</h3>
                <pre className="optimization-request-config-preview"><code>{JSON.stringify(strategyDetails, null, 2)}</code></pre>
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Request config</h3>
                <pre className="optimization-request-config-preview"><code>{JSON.stringify(job.request_config || {}, null, 2)}</code></pre>
              </div>

              <div className="panel card-pad optimization-detail-panel">
                <h3 className="t-h2">Normalized config</h3>
                <pre className="optimization-request-config-preview"><code>{JSON.stringify(job.normalized_config || {}, null, 2)}</code></pre>
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
  return <span className="plans-status">{status || "unknown"}</span>;
}

function Kpi({ label, value }) {
  return (
    <div className="runs-kpi">
      <div className="t-label">{label}</div>
      <div className="runs-kpi-value">{value}</div>
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
