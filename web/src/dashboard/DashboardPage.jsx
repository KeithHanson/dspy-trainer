import { useNavigate } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";
import { useDashboardOverview } from "./useDashboardOverview";

function statusLabel(status) {
  if (status === "running") return "Running";
  if (status === "complete") return "Complete";
  if (status === "failed") return "Failed";
  return "Queued";
}

export function DashboardPage({ adapter, onOpenRun, user }) {
  const navigate = useNavigate();
  const { isLoading, error, data } = useDashboardOverview(adapter);

  if (isLoading) {
    return <LoadingState label="Loading dashboard..." />;
  }

  if (error) {
    return <ErrorState title="Could not load dashboard" description={error.message} />;
  }

  const openRun = (runId) => {
    if (onOpenRun) {
      onOpenRun(runId);
      return;
    }
    navigate(`/runs/${runId}`);
  };

  const greetingName = user?.name?.split(" ")[0] || data.greetingName;

  const openLiveFromKeyboard = (event, runId) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openRun(runId);
    }
  };

  return (
    <section className="page">
      <div className="page-body dashboard-wrap">
        <header className="row between dashboard-head">
          <div className="col gap-1">
            <h1 className="t-display">Good morning, {greetingName}</h1>
            <p className="muted t-sm">{data.summaryLine}</p>
          </div>
          <div className="row gap-2">
            <Button onClick={() => navigate("/bundles?upload=1")}>Upload bundle</Button>
            <Button variant="primary" onClick={() => navigate("/plans?new=1")}>New plan</Button>
          </div>
        </header>

        {data.liveJob ? (
          <div
            className="dashboard-live"
            onClick={() => openRun(data.liveJob.id)}
            onKeyDown={(event) => openLiveFromKeyboard(event, data.liveJob.id)}
            role="button"
            tabIndex={0}
          >
            <div className="row between">
              <div className="col gap-1">
                <div className="t-h2">Live eval job: {data.liveJob.planName}</div>
                <div className="cap">{data.liveJob.bundleName} · {Math.round(data.liveJob.passRate * 100)}% pass</div>
              </div>
              <Button
                size="sm"
                variant="primary"
                onClick={(event) => {
                  event.stopPropagation();
                  openRun(data.liveJob.id);
                }}
              >
                Open live monitor
              </Button>
            </div>
            <div className="dashboard-progress-track" aria-hidden="true">
              <span className="dashboard-progress-fill" style={{ width: `${data.liveJob.progressPct}%` }} />
            </div>
          </div>
        ) : (
          <div className="panel dashboard-zero" role="status">No live eval job running.</div>
        )}

        <section className="dashboard-kpis">
          {data.kpis.map((kpi) => (
            <article key={kpi.id} className="panel card-pad col gap-2">
              <p className="t-label">{kpi.label}</p>
              <div className="row between">
                <p className="dashboard-kpi-value">{kpi.value}</p>
                <span className="dashboard-kpi-delta">{kpi.delta}</span>
              </div>
            </article>
          ))}
        </section>

        <section className="dashboard-section">
          <div className="row between dashboard-section-head">
            <h2 className="t-h2">Recent eval jobs</h2>
            <Button variant="ghost" size="sm" onClick={() => navigate("/runs")}>View all</Button>
          </div>
          {data.recentJobs.length === 0 ? (
            <EmptyState title="No recent jobs" description="Run your first plan to see job progress and quality metrics." />
          ) : (
            <div className="panel dashboard-table-wrap">
              <table className="dashboard-table">
                <thead>
                  <tr>
                    <th>Plan</th>
                    <th>Bundle</th>
                    <th>Status</th>
                    <th>Progress</th>
                    <th>Pass</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recentJobs.map((job) => (
                    <tr key={job.id} onClick={() => openRun(job.id)}>
                      <td>{job.planName}</td>
                      <td>{job.bundleName}</td>
                      <td>{statusLabel(job.status)}</td>
                      <td>{job.progress.done}/{job.progress.total}</td>
                      <td>{Math.round(job.passRate * 100)}%</td>
                      <td>{job.startedLabel}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="dashboard-two-up">
          <article className="panel card-pad">
            <div className="row between dashboard-section-head">
              <h2 className="t-h2">Needs attention</h2>
            </div>
            {data.alerts.length === 0 ? (
              <div className="dashboard-zero">No alerts right now.</div>
            ) : (
              <div className="col gap-2">
                {data.alerts.map((alert) => (
                  <div key={alert.id} className="dashboard-alert row between gap-2">
                    <div className="col gap-1">
                      <p className="t-sm">{alert.title}</p>
                      <p className="cap">{alert.detail}</p>
                    </div>
                    <Button size="sm" onClick={() => navigate(alert.ctaTo)}>{alert.ctaLabel}</Button>
                  </div>
                ))}
              </div>
            )}
          </article>

          <article className="panel card-pad col gap-2">
            <h2 className="t-h2">Quick start</h2>
            {data.quickStart.map((item) => (
              <button key={item.id} className="dashboard-quick" type="button" onClick={() => navigate(item.to)}>
                <span className="t-sm">{item.title}</span>
                <span className="cap">{item.detail}</span>
              </button>
            ))}
          </article>
        </section>
      </div>
    </section>
  );
}
