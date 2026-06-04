import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

export function RunsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const planId = searchParams.get("plan") || "";
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [plans, setPlans] = useState([]);
  const [workersData, setWorkersData] = useState({ items: [], total_workers: 0, reported_workers: 0, available_workers: 0, busy_workers: 0 });
  const [runPlan, setRunPlan] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [selectedTask, setSelectedTask] = useState(null);
  const [filter, setFilter] = useState("all");
  const [isLoading, setIsLoading] = useState(false);
  const [deletingPlanId, setDeletingPlanId] = useState("");
  const [error, setError] = useState("");
  const [workersError, setWorkersError] = useState("");
  const [profileNames, setProfileNames] = useState({});

  useEffect(() => {
    const loadProfiles = async () => {
      try {
        const response = await fetch(`${apiBase}/lm-profiles`, { method: "GET" });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (!Array.isArray(payload)) {
          return;
        }
        const next = {};
        payload.forEach((profile) => {
          if (profile?.id) {
            next[profile.id] = profile.name || profile.id;
          }
        });
        setProfileNames(next);
      } catch {
        setProfileNames({});
      }
    };
    loadProfiles();
  }, [apiBase]);

  const deleteRunPlan = async (id) => {
    const confirmed = window.confirm("Delete this run? This will remove all run items for it.");
    if (!confirmed) {
      return;
    }
    setDeletingPlanId(id);
    setError("");
    try {
      const response = await fetch(`${apiBase}/agent-run-plans/${id}`, { method: "DELETE" });
      if (!response.ok) {
        throw new Error(`Could not delete run (${response.status})`);
      }
      setPlans((current) => current.filter((plan) => plan.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete run");
    } finally {
      setDeletingPlanId("");
    }
  };

  useEffect(() => {
    const loadWorkers = async () => {
      try {
        const workersResp = await fetch(`${apiBase}/workers`, { method: "GET" });
        if (!workersResp.ok) {
          throw new Error(`Could not load workers (${workersResp.status})`);
        }
        const workersPayload = await workersResp.json();
        const normalized = normalizeWorkersPayload(workersPayload);
        setWorkersData(normalized);
        setWorkersError("");
      } catch (err) {
        setWorkersError(err instanceof Error ? err.message : "Could not load workers");
        setWorkersData({ items: [], total_workers: 0, reported_workers: 0, available_workers: 0, busy_workers: 0 });
      }
    };
    loadWorkers();
    const interval = setInterval(loadWorkers, 2000);
    return () => clearInterval(interval);
  }, [apiBase]);

  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      setError("");
      try {
        if (!planId) {
          const listResp = await fetch(`${apiBase}/agent-run-plans?limit=50&offset=0`, { method: "GET" });
          if (!listResp.ok) {
            throw new Error(`Could not load runs (${listResp.status})`);
          }
          const listPayload = await listResp.json();
          const listedPlans = Array.isArray(listPayload) ? listPayload : [];
          setPlans(listedPlans);
          setRunPlan(null);
          setTasks([]);
          return;
        }
        const runResp = await fetch(`${apiBase}/agent-run-plans/${planId}`, { method: "GET" });
        if (!runResp.ok) {
          throw new Error(`Could not load run plan (${runResp.status})`);
        }
        const runPayload = await runResp.json();
        setRunPlan(runPayload);
        const taskResp = await fetch(`${apiBase}/agent-run-plans/${planId}/tasks?limit=100&offset=0`, { method: "GET" });
        if (!taskResp.ok) {
          throw new Error(`Could not load run tasks (${taskResp.status})`);
        }
        const taskPayload = await taskResp.json();
        setTasks(Array.isArray(taskPayload.items) ? taskPayload.items : []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load eval run");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [apiBase, planId]);

  useEffect(() => {
    if (!planId) {
      return undefined;
    }
    const interval = setInterval(async () => {
      try {
        const runResp = await fetch(`${apiBase}/agent-run-plans/${planId}`, { method: "GET" });
        const taskResp = await fetch(`${apiBase}/agent-run-plans/${planId}/tasks?limit=500&offset=0`, { method: "GET" });
        if (runResp.ok) {
          setRunPlan(await runResp.json());
        }
        if (taskResp.ok) {
          const taskPayload = await taskResp.json();
          setTasks(Array.isArray(taskPayload.items) ? taskPayload.items : []);
        }
      } catch {
        // background polling is best-effort
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [apiBase, planId]);

  if (!planId) {
    return (
      <section className="page">
        <div className="page-body plans-wrap">
          <header className="row between plans-head">
            <div className="col gap-1">
              <h1 className="t-display" style={{ fontSize: 22 }}>Runs</h1>
              <p className="muted t-sm">Each job is one execution of a saved evaluation plan.</p>
            </div>
            <Button onClick={() => navigate("/plans")}>Back to plans</Button>
          </header>
          {isLoading ? <LoadingState label="Loading runs..." /> : null}
          {error ? <ErrorState title="Could not load runs" description={error} /> : null}
          {!isLoading && !error && plans.length ? (
            <>
              <section className="panel" style={{ overflow: "hidden" }}>
                <table className="dashboard-table">
                  <thead>
                    <tr>
                      <th>Plan Name</th>
                      <th>LM Profile</th>
                      <th>Plan ID</th>
                      <th>Run Status</th>
                      <th>Successful Runs</th>
                      <th>Errors</th>
                      <th>Average Score</th>
                      <th>Started</th>
                      <th></th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {plans.map((plan) => (
                      <tr key={plan.id} className="runs-row-click" onClick={() => navigate(`/runs?plan=${encodeURIComponent(plan.id)}`)}>
                        <td>{plan.plan_name || "RunPlan"}</td>
                        <td className="cap">{plan.lm_profile_id ? (profileNames[plan.lm_profile_id] || plan.lm_profile_id) : "none"}</td>
                        <td className="mono">{shortId(plan.id)}</td>
                        <td><StatusPill status={plan.status} /></td>
                        <td className="mono">{plan.completed_tasks ?? 0}/{plan.total_tasks ?? 0}</td>
                        <td className="mono">{plan.failed_tasks ?? 0}/{plan.total_tasks ?? 0}</td>
                        <td className={`mono ${toneForAverageScore(plan.average_score, plan.score_pass_threshold)}`}>{typeof plan.average_score === "number" ? plan.average_score.toFixed(3) : "-"}</td>
                        <td className="cap">{formatTimeAgo(plan.created_at)}</td>
                        <td><Icon name="chevR" size={14} className="faint" /></td>
                        <td>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={(event) => {
                              event.stopPropagation();
                              deleteRunPlan(plan.id);
                            }}
                            disabled={deletingPlanId === plan.id}
                          >
                            {deletingPlanId === plan.id ? "Deleting..." : "Delete"}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          ) : !isLoading && !error ? (
            <EmptyState title="No runs yet" description="Run a plan from Evaluation Plans to see runs here." />
          ) : null}

          <WorkersSection workersData={workersData} workersError={workersError} />
        </div>
      </section>
    );
  }

  const counts = taskCounts(tasks);
  const totalRuns = Number(runPlan?.total_tasks ?? tasks.length ?? 0);
  const filteredTasks = tasks.filter((task) => (filter === "all" ? true : task.status === filter));

  return (
    <section className="page">
      <div className="page-body plans-wrap">
        <header className="row between plans-head">
          <div className="col gap-1">
              <h1 className="t-display" style={{ fontSize: 22 }}>Runs</h1>
            <p className="muted t-sm">Live run monitor for one evaluation plan execution.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={() => navigate("/runs")}>All jobs</Button>
            <Button onClick={() => navigate("/plans")}>Back to plans</Button>
          </div>
        </header>

        {isLoading ? <LoadingState label="Loading run monitor..." /> : null}
        {error ? <ErrorState title="Could not load run" description={error} /> : null}

        {!isLoading && !error && runPlan ? (
          <>
            <section className="panel card-pad plans-form-block">
              <div className="row between" style={{ marginBottom: 8 }}>
                <h2 className="t-h2">Run summary</h2>
                <StatusPill status={runPlan.status} />
              </div>
              <p className="cap mono" style={{ marginBottom: 8 }}>Plan: {runPlan.plan_name || "RunPlan"}</p>
              <p className="cap mono" style={{ marginBottom: 8 }}>LM profile: {runPlan.lm_profile_id ? (profileNames[runPlan.lm_profile_id] || runPlan.lm_profile_id) : "none"}</p>
              <div className="runs-kpis">
                <Kpi
                  label="Pass/Fail"
                  value={(
                    <span className="mono runs-passfail-value">
                      <span className="runs-passfail-pass">{counts.evalPass}</span>
                      <span className="runs-passfail-sep">/</span>
                      <span className="runs-passfail-fail">{counts.evalFail}</span>
                    </span>
                  )}
                />
                <Kpi label="Successful Runs" value={`${counts.succeeded}/${totalRuns}`} tone="pass" />
                <Kpi label="Errors" value={`${counts.failed}/${totalRuns}`} tone="fail" />
                <Kpi label="Running" value={counts.running} tone="run" />
              </div>
              <p className="cap mono" style={{ marginTop: 10 }}>Run ID: {runPlan.id}</p>
              {runPlan.mlflow_parent_run_id ? (
                <p className="cap" style={{ marginTop: 8 }}>
                  <a
                    className="runs-mlflow-link"
                    href={`${(import.meta.env.VITE_MLFLOW_BASE_URL || "http://localhost:5001").replace(/\/$/, "")}/#/experiments/${encodeURIComponent(runPlan.mlflow_experiment_id || "0")}/runs/${encodeURIComponent(runPlan.mlflow_parent_run_id)}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open MLflow eval run
                  </a>
                </p>
              ) : null}
            </section>

            <div className="row between" style={{ marginBottom: 10 }}>
              <div className="row gap-2"><h2 className="t-h2">Eval Run Items</h2><span className="plans-count">{filteredTasks.length}</span></div>
              <div className="row gap-2">
                {[
                  ["all", "All"],
                  ["succeeded", "Succeeded"],
                  ["failed", "Errored"],
                  ["running", "Running"],
                ].map(([value, label]) => (
                  <Button key={value} size="sm" variant={filter === value ? "primary" : "ghost"} onClick={() => setFilter(value)}>{label}</Button>
                ))}
              </div>
            </div>

            <section className="panel" style={{ overflow: "hidden" }}>
              <table className="dashboard-table">
                <thead>
                    <tr>
                      <th style={{ width: 36 }}></th>
                      <th>Question</th>
                      <th>Attempt</th>
                      <th>Status</th>
                      <th>Eval</th>
                      <th>Score</th>
                      <th>Worker</th>
                    </tr>
                </thead>
                <tbody>
                  {filteredTasks.map((task) => (
                    <tr key={task.id} className="runs-row-click" onClick={() => setSelectedTask(task)}>
                      <td><StatusDot status={task.status} /></td>
                      <td className="mono">Q{task.question_index + 1}</td>
                      <td className="mono">{task.attempt_index + 1}</td>
                      <td><StatusPill status={task.status} /></td>
                      <td className="mono">{task.status === "succeeded" ? (task.eval_pass ? "pass" : "fail") : "-"}</td>
                      <td className="mono">{task.score ?? "-"}</td>
                      <td className="mono">{task.worker_id || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!filteredTasks.length ? <div className="dashboard-zero">No tasks for this filter yet.</div> : null}
            </section>

          </>
        ) : null}

        <WorkersSection workersData={workersData} workersError={workersError} />

        {selectedTask ? (
          <div className="runs-drawer-backdrop" onClick={() => setSelectedTask(null)}>
            <aside className="runs-drawer panel" onClick={(event) => event.stopPropagation()}>
              <div className="row between" style={{ marginBottom: 10 }}>
                <h3 className="t-h2">Run item detail</h3>
                <Button size="sm" variant="ghost" onClick={() => setSelectedTask(null)}>Close</Button>
              </div>
              <div className="col gap-2">
                <section className="runs-drawer-summary">
                  <div className="runs-drawer-summary-grid">
                    <div className="runs-drawer-stat">
                      <span className="runs-drawer-stat-label">Status</span>
                      <span><StatusPill status={selectedTask.status} /></span>
                    </div>
                    <div className="runs-drawer-stat">
                      <span className="runs-drawer-stat-label">Score</span>
                      <span className="runs-drawer-stat-value mono">{selectedTask.score ?? "-"}</span>
                    </div>
                    <div className="runs-drawer-stat">
                      <span className="runs-drawer-stat-label">Eval</span>
                      <span className="runs-drawer-stat-value mono">{selectedTask.status === "succeeded" ? (selectedTask.eval_pass ? "✅" : "❌") : "-"}</span>
                    </div>
                    <div className="runs-drawer-stat">
                      <span className="runs-drawer-stat-label">Worker</span>
                      <span className="runs-drawer-stat-value mono">{selectedTask.worker_id || "-"}</span>
                    </div>
                    <div className="runs-drawer-stat">
                      <span className="runs-drawer-stat-label">Error</span>
                      <span className="runs-drawer-stat-value mono">{selectedTask.error || "-"}</span>
                    </div>
                  </div>
                  <div className="runs-drawer-rationale">
                    <div className="runs-drawer-stat-label">Rationale</div>
                    <p className="runs-drawer-rationale-copy">{selectedTask.rationale || "-"}</p>
                  </div>
                </section>
                <hr className="hr" />
                <div>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div className="t-label">Input payload</div>
                    <CopyButton value={JSON.stringify(selectedTask.input_payload || {}, null, 2)} />
                  </div>
                  <pre className="bundles-snippet"><code>{JSON.stringify(selectedTask.input_payload || {}, null, 2)}</code></pre>
                </div>
                <div>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div className="t-label">Label payload</div>
                    <CopyButton value={JSON.stringify(selectedTask.label_payload || {}, null, 2)} />
                  </div>
                  <pre className="bundles-snippet"><code>{JSON.stringify(selectedTask.label_payload || {}, null, 2)}</code></pre>
                </div>
                <div>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div className="t-label">Prediction payload</div>
                    <CopyButton value={JSON.stringify(selectedTask.prediction_payload || {}, null, 2)} />
                  </div>
                  <pre className="bundles-snippet"><code>{JSON.stringify(selectedTask.prediction_payload || {}, null, 2)}</code></pre>
                </div>
                <div>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div className="t-label">Worker log</div>
                    <CopyButton value={selectedTask.worker_log || "No worker log recorded yet."} />
                  </div>
                  <pre className="bundles-snippet"><code>{selectedTask.worker_log || "No worker log recorded yet."}</code></pre>
                </div>
              </div>
            </aside>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function WorkersSection({ workersData, workersError }) {
  const workers = Array.isArray(workersData?.items) ? workersData.items : [];
  const totalWorkers = Number(workersData?.total_workers || 0);
  const availableWorkers = Number(workersData?.available_workers || 0);
  const busyWorkers = Number(workersData?.busy_workers || 0);
  const missingWorkers = Math.max(0, totalWorkers - Number(workersData?.reported_workers || workers.length));

  return (
    <section className="panel card-pad runs-workers-section">
      <div className="row between" style={{ gap: 12, marginBottom: 10, alignItems: "flex-start" }}>
        <div>
          <h3 className="t-h2" style={{ marginBottom: 6 }}>Workers</h3>
          <p className="muted t-sm">
            {availableWorkers} available of {totalWorkers || workers.length} total
            {busyWorkers ? ` · ${busyWorkers} busy` : ""}
            {missingWorkers ? ` · ${missingWorkers} not reporting` : ""}
          </p>
        </div>
      </div>
      {workersError ? <p className="cap" style={{ marginBottom: 6 }}>Worker refresh issue: {workersError}</p> : null}
      {!workers.length ? (
        <div className="dashboard-zero">No workers reported yet.</div>
      ) : (
        <div className="runs-workers-grid">
          {workers.map((worker) => (
            <article key={worker.worker_id} className="runs-worker-card">
              <div className="row between" style={{ gap: 10, alignItems: "center" }}>
                <div className="col gap-1" style={{ minWidth: 0 }}>
                  <div className="mono cap" style={{ overflowWrap: "anywhere" }}>{worker.worker_id}</div>
                  <div className="muted t-xs">Last seen {worker.last_seen ? formatTimeAgo(worker.last_seen) : "unknown"}</div>
                </div>
                <StatusPill status={worker.status} />
              </div>
              <dl className="runs-worker-meta">
                <div>
                  <dt>Task</dt>
                  <dd className="mono">{worker.task_id ? shortId(worker.task_id) : "Idle"}</dd>
                </div>
                <div>
                  <dt>State</dt>
                  <dd>{describeWorkerState(worker.status, worker.task_id)}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function normalizeWorkersPayload(payload) {
  if (Array.isArray(payload)) {
    const availableWorkers = payload.filter((worker) => worker?.status === "listening").length;
    return {
      items: payload,
      total_workers: payload.length,
      reported_workers: payload.length,
      available_workers: availableWorkers,
      busy_workers: Math.max(0, payload.length - availableWorkers),
    };
  }
  const items = Array.isArray(payload?.items) ? payload.items : [];
  return {
    items,
    total_workers: Number(payload?.total_workers || items.length),
    reported_workers: Number(payload?.reported_workers || items.length),
    available_workers: Number(payload?.available_workers || 0),
    busy_workers: Number(payload?.busy_workers || 0),
  };
}

function describeWorkerState(status, taskId) {
  if (status === "listening") return "Ready for the next task";
  if (status === "running") return taskId ? "Actively processing work" : "Busy";
  return "Heartbeat reported";
}

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Button size="sm" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
  );
}

function taskCounts(tasks) {
  const succeeded = tasks.filter((task) => task.status === "succeeded").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  const running = tasks.filter((task) => task.status === "running").length;
  const queued = tasks.filter((task) => task.status === "queued" || task.status === "pending").length;
  const evalPass = tasks.filter((task) => task.status === "succeeded" && task.eval_pass === true).length;
  const evalFail = tasks.filter((task) => task.status === "succeeded" && task.eval_pass === false).length;
  return {
    succeeded,
    failed,
    running,
    queued,
    evalPass,
    evalFail,
    done: succeeded + failed,
  };
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
  return <span className={`plans-status ${toneClass}`}>{status}</span>;
}

function StatusDot({ status }) {
  const cls = status === "failed" ? "runs-dot-fail" : status === "succeeded" ? "runs-dot-pass" : status === "running" ? "runs-dot-run" : "runs-dot-queued";
  return <span className={`runs-dot ${cls}`} />;
}

function Kpi({ label, value, tone }) {
  return (
    <div className="runs-kpi">
      <div className="t-label">{label}</div>
      <div className={`runs-kpi-value ${tone ? `runs-kpi-${tone}` : ""}`}>{value}</div>
    </div>
  );
}

function toneForAverageScore(value, threshold) {
  if (!Number.isFinite(Number(value)) || !Number.isFinite(Number(threshold))) {
    return "";
  }
  return Number(value) >= Number(threshold) ? "runs-kpi-pass" : "runs-kpi-fail";
}

function shortId(value) {
  return typeof value === "string" ? value.slice(0, 8) : "-";
}

function formatTimeAgo(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
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
