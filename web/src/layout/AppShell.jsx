import { useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import { Icon } from "../components/Icon";

const PRIMARY_NAV = [
  { to: "/dashboard", label: "Overview", icon: "grid" },
  { to: "/lm-profiles", label: "LM Profiles", icon: "settings" },
  { to: "/bundles", label: "Module Bundles", icon: "box" },
  { to: "/plans", label: "Evaluation Plans", icon: "layers" },
  { to: "/runs", label: "Eval Runs", icon: "activity" },
  { to: "/optimization", label: "Optimization", icon: "search" },
  { to: "/optimization/jobs", label: "Optimization Jobs", icon: "activity" },
];

const SECONDARY_NAV = [];

function NavSection({ items, hasActiveRun, hasActiveOptimization }) {
  return items.map((item) => (
    <NavLink
      end
      key={item.to}
      className={({ isActive }) => (isActive ? "shell-nav-item shell-nav-item-active" : "shell-nav-item")}
      to={item.to}
    >
      {({ isActive }) => (
        <>
          <Icon className="shell-nav-icon" name={item.icon} size={item.icon === "settings" ? 18 : 16} active={isActive} />
          <span>{item.label}</span>
          {item.to === "/runs" && hasActiveRun ? <span className="dot d-live" aria-hidden="true" /> : null}
          {item.to === "/optimization/jobs" && hasActiveOptimization ? <span className="dot d-live" aria-hidden="true" /> : null}
        </>
      )}
    </NavLink>
  ));
}

export function AppShell({ children }) {
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const mlflowBase = useMemo(() => (import.meta.env.VITE_MLFLOW_BASE_URL || "http://localhost:5001").replace(/\/$/, ""), []);
  const litellmBase = useMemo(() => (import.meta.env.VITE_LITELLM_BASE_URL || "http://localhost:4000").replace(/\/$/, ""), []);
  const [hasActiveRun, setHasActiveRun] = useState(false);
  const [hasActiveOptimization, setHasActiveOptimization] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const loadRunActivity = async () => {
      try {
        const [runsResp, optimizationResp] = await Promise.all([
          fetch(`${apiBase}/agent-run-plans?limit=50&offset=0`, { method: "GET" }),
          fetch(`${apiBase}/optimization/jobs?limit=50&offset=0`, { method: "GET" }),
        ]);
        if (!runsResp.ok || !optimizationResp.ok) {
          if (isMounted) {
            setHasActiveRun(false);
            setHasActiveOptimization(false);
          }
          return;
        }
        const [runPayload, optimizationPayload] = await Promise.all([runsResp.json(), optimizationResp.json()]);
        const plans = Array.isArray(runPayload) ? runPayload : [];
        const jobs = Array.isArray(optimizationPayload) ? optimizationPayload : [];
        const nextHasActive = plans.some(
          (plan) => plan?.status === "queued" || plan?.status === "running" || Number(plan?.running_tasks || 0) > 0,
        );
        const nextHasActiveOptimization = jobs.some((job) => job?.status === "queued" || job?.status === "running");
        if (isMounted) {
          setHasActiveRun(nextHasActive);
          setHasActiveOptimization(nextHasActiveOptimization);
        }
      } catch {
        if (isMounted) {
          setHasActiveRun(false);
          setHasActiveOptimization(false);
        }
      }
    };

    loadRunActivity();
    const interval = setInterval(loadRunActivity, 10000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [apiBase]);

  return (
    <div className="shell-root">
      <aside className="shell-sidebar col">
        <div className="shell-org row gap-3">
          <div className="shell-logo center">
            <Icon name="bolt" size={14} />
          </div>
          <span className="shell-org-name">dspy-trainer</span>
        </div>

        <div className="col shell-nav-wrap">
          <NavSection items={PRIMARY_NAV} hasActiveRun={hasActiveRun} hasActiveOptimization={hasActiveOptimization} />
          <hr className="hr shell-divider" />
          {SECONDARY_NAV.length ? <NavSection items={SECONDARY_NAV} hasActiveRun={false} hasActiveOptimization={false} /> : null}
          <div className="col shell-external-links">
            <a className="shell-nav-item" href={mlflowBase} target="_blank" rel="noreferrer">
              <Icon className="shell-nav-icon" name="external" size={16} />
              <span>MLFlow</span>
            </a>
            <a className="shell-nav-item" href={litellmBase} target="_blank" rel="noreferrer">
              <Icon className="shell-nav-icon" name="external" size={16} />
              <span>LiteLLM Proxy</span>
            </a>
          </div>
        </div>
      </aside>

      <main className="shell-main col">
        <section className="shell-content">{children}</section>
      </main>
    </div>
  );
}
