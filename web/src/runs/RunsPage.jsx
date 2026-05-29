import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

export function RunsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const planId = searchParams.get("plan") || "";
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [runPlan, setRunPlan] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      if (!planId) {
        return;
      }
      setIsLoading(true);
      setError("");
      try {
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

  if (!planId) {
    return (
      <section className="page">
        <div className="page-body">
          <EmptyState title="No run selected" description="Start a run from Evaluation Plans to monitor progress here." />
        </div>
      </section>
    );
  }

  return (
    <section className="page">
      <div className="page-body plans-wrap">
        <header className="row between plans-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Eval Jobs</h1>
            <p className="muted t-sm">Each run executes one saved plan against its selected module bundle.</p>
          </div>
          <Button onClick={() => navigate("/plans")}>Back to plans</Button>
        </header>

        {isLoading ? <LoadingState label="Loading run monitor..." /> : null}
        {error ? <ErrorState title="Could not load run" description={error} /> : null}

        {!isLoading && !error && runPlan ? (
          <>
            <section className="panel card-pad plans-form-block">
              <div className="row between" style={{ marginBottom: 8 }}>
                <h2 className="t-h2">Run summary</h2>
                <span className="plans-status">{runPlan.status}</span>
              </div>
              <p className="cap mono">Run ID: {runPlan.id}</p>
              <p className="cap mono">Tasks: {runPlan.completed_tasks}/{runPlan.total_tasks} complete · {runPlan.failed_tasks} failed</p>
            </section>

            <section className="panel" style={{ overflow: "hidden" }}>
              <table className="dashboard-table">
                <thead>
                  <tr>
                    <th>Question #</th>
                    <th>Attempt</th>
                    <th>Status</th>
                    <th>Score</th>
                    <th>Worker</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((task) => (
                    <tr key={task.id}>
                      <td>{task.question_index + 1}</td>
                      <td className="mono">{task.attempt_index + 1}</td>
                      <td><span className="plans-status">{task.status}</span></td>
                      <td className="mono">{task.score ?? "-"}</td>
                      <td className="mono">{task.worker_id || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!tasks.length ? <div className="dashboard-zero">No tasks queued yet. Refresh in a few seconds.</div> : null}
            </section>
          </>
        ) : null}
      </div>
    </section>
  );
}
