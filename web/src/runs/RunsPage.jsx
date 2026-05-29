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
  const [workers, setWorkers] = useState([]);
  const [runPlan, setRunPlan] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [selectedTask, setSelectedTask] = useState(null);
  const [filter, setFilter] = useState("all");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      setError("");
      try {
        if (!planId) {
          const listResp = await fetch(`${apiBase}/agent-run-plans?limit=50&offset=0`, { method: "GET" });
          if (!listResp.ok) {
            throw new Error(`Could not load eval jobs (${listResp.status})`);
          }
          const listPayload = await listResp.json();
          const listedPlans = Array.isArray(listPayload) ? listPayload : [];
          setPlans(listedPlans);
          const workersResp = await fetch(`${apiBase}/workers`, { method: "GET" });
          if (workersResp.ok) {
            const workersPayload = await workersResp.json();
            setWorkers(Array.isArray(workersPayload) ? workersPayload : []);
          } else {
            setWorkers([]);
          }
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

  if (!planId && !isLoading && !error) {
    return (
      <section className="page">
        <div className="page-body plans-wrap">
          <header className="row between plans-head">
            <div className="col gap-1">
              <h1 className="t-display" style={{ fontSize: 22 }}>Eval Jobs</h1>
              <p className="muted t-sm">Each job is one execution of a saved evaluation plan.</p>
            </div>
            <Button onClick={() => navigate("/plans")}>Back to plans</Button>
          </header>
          {plans.length ? (
            <>
              <section className="panel" style={{ overflow: "hidden" }}>
                <table className="dashboard-table">
                  <thead>
                    <tr>
                      <th>Plan ID</th>
                      <th>Run Status</th>
                      <th>Successful Runs</th>
                      <th>Errors</th>
                      <th>Average Score</th>
                      <th>Started</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {plans.map((plan) => (
                      <tr key={plan.id} className="runs-row-click" onClick={() => navigate(`/runs?plan=${encodeURIComponent(plan.id)}`)}>
                        <td className="mono">{shortId(plan.id)}</td>
                        <td><StatusPill status={plan.status} /></td>
                        <td className="mono">{plan.completed_tasks ?? 0}/{plan.total_tasks ?? 0}</td>
                        <td className="mono">{plan.failed_tasks ?? 0}/{plan.total_tasks ?? 0}</td>
                        <td className="mono">{typeof plan.average_score === "number" ? plan.average_score.toFixed(3) : "-"}</td>
                        <td className="cap">{formatTimeAgo(plan.created_at)}</td>
                        <td><Icon name="chevR" size={14} className="faint" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="panel card-pad runs-workers-section">
                <h3 className="t-h2" style={{ marginBottom: 10 }}>Workers</h3>
                {!workers.length ? (
                  <div className="dashboard-zero">No workers reported yet.</div>
                ) : (
                  <div className="runs-workers-inline">
                    {workers.map((worker) => (
                      <span key={worker.worker_id} className="runs-workers-inline-item">
                        <span className="mono cap">{worker.worker_id}</span>
                        <span className="cap">{worker.status}{worker.task_id ? ` · ${shortId(worker.task_id)}` : ""}{worker.last_seen ? ` · seen ${formatTimeAgo(worker.last_seen)}` : ""}</span>
                      </span>
                    ))}
                  </div>
                )}
              </section>
            </>
          ) : (
            <EmptyState title="No eval jobs yet" description="Run a plan from Evaluation Plans to see jobs here." />
          )}
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
            <h1 className="t-display" style={{ fontSize: 22 }}>Eval Jobs</h1>
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
              <div className="runs-kpis">
                <Kpi label="Successful Runs" value={`${counts.succeeded}/${totalRuns}`} tone="pass" />
                <Kpi label="Completed" value={counts.done} />
                <Kpi label="Errors" value={`${counts.failed}/${totalRuns}`} tone="fail" />
                <Kpi label="Running" value={counts.running} tone="run" />
              </div>
              <p className="cap mono" style={{ marginTop: 10 }}>Run ID: {runPlan.id}</p>
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

        {selectedTask ? (
          <div className="runs-drawer-backdrop" onClick={() => setSelectedTask(null)}>
            <aside className="runs-drawer panel" onClick={(event) => event.stopPropagation()}>
              <div className="row between" style={{ marginBottom: 10 }}>
                <h3 className="t-h2">Run item detail</h3>
                <Button size="sm" variant="ghost" onClick={() => setSelectedTask(null)}>Close</Button>
              </div>
              <div className="col gap-2">
                <div className="row between"><span className="cap">Status</span><StatusPill status={selectedTask.status} /></div>
                <div className="row between"><span className="cap">Score</span><span className="mono">{selectedTask.score ?? "-"}</span></div>
                <div className="row between"><span className="cap">Worker</span><span className="mono">{selectedTask.worker_id || "-"}</span></div>
                <div className="row between"><span className="cap">Error</span><span className="mono">{selectedTask.error || "-"}</span></div>
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
  return {
    succeeded,
    failed,
    running,
    queued,
    done: succeeded + failed,
  };
}

function StatusPill({ status }) {
  return <span className="plans-status">{status}</span>;
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
