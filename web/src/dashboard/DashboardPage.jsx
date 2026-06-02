import { useNavigate } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";
import { useDashboardOverview } from "./useDashboardOverview";

function statusLabel(status) {
  if (status === "running") return "Running";
  if (status === "succeeded" || status === "complete") return "Complete";
  if (status === "failed") return "Failed";
  return "Queued";
}

function greetingForLocalTime() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
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
    navigate(`/runs?plan=${encodeURIComponent(runId)}`);
  };

  const greetingName = user?.name?.split(" ")[0] || data.greetingName;

  const openLiveFromKeyboard = (event, runId) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openRun(runId);
    }
  };

  const totalCount = Math.max(0, Number(data.spotlightJob?.totalCount || 0));
  const passPct = totalCount ? (Math.max(0, Number(data.spotlightJob?.passCount || 0)) / totalCount) * 100 : 0;
  const failPct = totalCount ? (Math.max(0, Number(data.spotlightJob?.failCount || 0)) / totalCount) * 100 : 0;
  const errorPct = totalCount ? (Math.max(0, Number(data.spotlightJob?.errorCount || 0)) / totalCount) * 100 : 0;
  const remainingPct = Math.max(0, 100 - passPct - failPct - errorPct);

  return (
    <section className="page">
      <div className="page-body dashboard-wrap">
        <header className="row between dashboard-head">
          <div className="col gap-1">
            <h1 className="t-display">{greetingForLocalTime()}, {greetingName}</h1>
            <p className="muted t-sm">{data.summaryLine}</p>
          </div>
          <div className="row gap-2">
            <Button onClick={() => navigate("/bundles?upload=1")}>Upload bundle</Button>
            <Button variant="primary" onClick={() => navigate("/plans?new=1")}>New plan</Button>
          </div>
        </header>

        {data.spotlightJob ? (
          <div
            className="dashboard-live"
            onClick={() => openRun(data.spotlightJob.id)}
            onKeyDown={(event) => openLiveFromKeyboard(event, data.spotlightJob.id)}
            role="button"
            tabIndex={0}
          >
            <div className="row between">
              <div className="col gap-1">
                <div className="t-h2">
                  {data.liveJob ? "Live run" : "Most recent run"}: {data.spotlightJob.planName}
                </div>
                <div className="cap">{data.spotlightJob.bundleName}</div>
                <div className="dashboard-live-breakdown">
                  <span className="dashboard-pill dashboard-pill-pass">Passes: {data.spotlightJob.passCount}</span>
                  <span className="dashboard-pill dashboard-pill-fail">Fails: {data.spotlightJob.failCount}</span>
                  <span className="dashboard-pill dashboard-pill-error">Errors: {data.spotlightJob.errorCount}</span>
                </div>
              </div>
              <Button
                size="sm"
                variant="primary"
                onClick={(event) => {
                  event.stopPropagation();
                  openRun(data.spotlightJob.id);
                }}
              >
                {data.liveJob ? "Open live monitor" : "Open run details"}
              </Button>
            </div>
            <div className="dashboard-progress-track" aria-hidden="true">
              <span className="dashboard-progress-segment dashboard-progress-pass" style={{ width: `${passPct}%` }} />
              <span className="dashboard-progress-segment dashboard-progress-fail" style={{ width: `${failPct}%` }} />
              <span className="dashboard-progress-segment dashboard-progress-error" style={{ width: `${errorPct}%` }} />
              <span className="dashboard-progress-segment dashboard-progress-remaining" style={{ width: `${remainingPct}%` }} />
            </div>
          </div>
        ) : (
           <div className="panel dashboard-zero dashboard-live-empty" role="status">No runs yet.</div>
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
              <h2 className="t-h2">Recent runs</h2>
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
