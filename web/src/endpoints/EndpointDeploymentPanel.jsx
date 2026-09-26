import { useEffect, useState } from "react";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const NONTERMINAL_PHASES = new Set(["legacy_static", "pending", "rolling", "draining", "rollback"]);

function safeOperatorText(value) {
  return String(value || "")
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]")
    .replace(/((?:password|secret|token|api[_-]?key|authorization|credential)[A-Za-z0-9_.-]*\s*[:=]\s*)[^\r\n]*/gi, "$1[REDACTED]")
    .slice(0, 4_096);
}

function shortIdentity(value) {
  if (!value) return "Not set";
  const text = String(value);
  return text.length > 24 ? `${text.slice(0, 12)}…${text.slice(-8)}` : text;
}

function statusTone(value) {
  const normalized = String(value || "unknown").toLowerCase();
  if (["ready", "managed"].includes(normalized)) return "runs-status-pill-pass";
  if (["failed", "rollout_failed", "migration_failed"].includes(normalized)) return "runs-status-pill-fail";
  if (["pending", "rolling", "draining", "rollback", "rolling_back", "warming_managed"].includes(normalized)) return "runs-status-pill-run";
  return "runs-status-pill-neutral";
}

function StatusPill({ value }) {
  return <span className={`plans-status ${statusTone(value)}`}>{String(value || "unknown").replaceAll("_", " ")}</span>;
}

function DeploymentReference({ label, reference, legacyFallback }) {
  return (
    <article className="endpoint-deployment-ref">
      <div className="row between">
        <strong>{label}</strong>
        {reference ? <StatusPill value={reference.build_status} /> : null}
      </div>
      {reference ? (
        <dl className="endpoint-deployment-meta">
          <div><dt>Revision</dt><dd className="mono" title={reference.revision_id || ""}>{shortIdentity(reference.revision_id)}</dd></div>
          <div><dt>Build</dt><dd className="mono" title={reference.build_id || ""}>{shortIdentity(reference.build_id)}</dd></div>
          <div><dt>Module</dt><dd className="mono" title={reference.module_import_id || ""}>{shortIdentity(reference.module_import_id)}</dd></div>
        </dl>
      ) : (
        <p className="cap">{legacyFallback && label === "Active" ? "Legacy static workers remain active until the managed image is ready." : "No image recorded."}</p>
      )}
      {reference?.failure_reason ? <p className="endpoint-deployment-reason">{safeOperatorText(reference.failure_reason)}</p> : null}
    </article>
  );
}

export function EndpointDeploymentPanel({ apiBase, endpointId, refreshKey = 0 }) {
  const [deployment, setDeployment] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const requestDeployment = async (signal) => {
    const response = await fetch(`${apiBase}/bundle-endpoints/${encodeURIComponent(endpointId)}/deployment`, {
      method: "GET",
      signal,
    });
    if (!response.ok) {
      let message = `Could not load rollout status (${response.status})`;
      try {
        const payload = await response.json();
        if (payload?.error) message = `${message}: ${safeOperatorText(payload.error)}`;
      } catch {
        // Use the stable fallback without rendering an arbitrary response body.
      }
      throw new Error(message);
    }
    return response.json();
  };

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError("");
    requestDeployment(controller.signal)
      .then(setDeployment)
      .catch((loadError) => {
        if (loadError?.name !== "AbortError") {
          setError(loadError instanceof Error ? loadError.message : "Could not load rollout status");
        }
      })
      .finally(() => setIsLoading(false));
    return () => controller.abort();
  }, [apiBase, endpointId, refreshKey]);

  const isNonterminal = NONTERMINAL_PHASES.has(String(deployment?.phase || ""));
  useEffect(() => {
    if (!isNonterminal) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      try {
        const next = await requestDeployment();
        if (!cancelled) {
          setDeployment(next);
          setError("");
        }
      } catch (pollError) {
        if (!cancelled) {
          setError(pollError instanceof Error ? pollError.message : "Could not refresh rollout status");
        }
      }
      if (!cancelled) timer = window.setTimeout(poll, 2_000);
    };
    timer = window.setTimeout(poll, 2_000);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [apiBase, endpointId, isNonterminal]);

  if (isLoading && !deployment) {
    return <section className="panel card-pad bundles-section"><LoadingState label="Loading rollout status..." /></section>;
  }
  if (!deployment) {
    return <section className="panel card-pad bundles-section"><ErrorState title="Rollout status unavailable" description={error || "No deployment state was returned."} /></section>;
  }

  const slots = Array.isArray(deployment.slots) ? deployment.slots : [];
  const targetBuildId = deployment.target?.build_id || null;
  const readyTargetSlots = slots.filter((slot) => slot.ready && (!targetBuildId || slot.build_id === targetBuildId)).length;
  const revisions = new Set(slots.map((slot) => slot.revision_id).filter(Boolean));
  const mixedRevisions = revisions.size > 1;
  const reason = deployment.rollback_reason || deployment.rollout_reason || deployment.failure_reason || "";

  return (
    <section className="panel card-pad bundles-section endpoint-deployment-panel" aria-label="Endpoint rollout status">
      <div className="row between endpoint-deployment-head">
        <div>
          <h2 className="t-h2">Image rollout</h2>
          <p className="cap">Generation {deployment.rollout_generation ?? 0} · {readyTargetSlots} of {deployment.desired_replica_count ?? 0} desired slots ready</p>
        </div>
        <div className="row gap-2">
          <StatusPill value={deployment.phase} />
          <StatusPill value={deployment.migration_state} />
        </div>
      </div>
      {error ? <ErrorState title="Rollout refresh failed" description={error} /> : null}
      {reason ? (
        <div className="endpoint-deployment-reason" role="alert">
          <strong>{deployment.phase === "rollback" ? "Rollback reason" : "Rollout reason"}</strong>
          <span>{safeOperatorText(reason)}</span>
        </div>
      ) : null}
      {mixedRevisions ? <div className="endpoint-mixed-banner" role="status">Mixed revisions are serving during this rolling transition.</div> : null}
      {deployment.failed_target ? <div className="endpoint-failed-banner" role="status">The replacement failed. Active capacity remains on the old build while rollback or recovery proceeds.</div> : null}
      <div className="endpoint-deployment-refs">
        <DeploymentReference label="Active" reference={deployment.active} legacyFallback={deployment.legacy_fallback} />
        <DeploymentReference label="Target" reference={deployment.target} legacyFallback={deployment.legacy_fallback} />
        <DeploymentReference label="Previous" reference={deployment.previous} legacyFallback={deployment.legacy_fallback} />
        {deployment.failed_target ? <DeploymentReference label="Failed target" reference={deployment.failed_target} legacyFallback={deployment.legacy_fallback} /> : null}
      </div>
      <div className="endpoint-slots-head row between">
        <h3 className="t-h2">Deployment slots</h3>
        <span className="cap">{slots.length} observed</span>
      </div>
      {!slots.length ? <p className="cap">No managed slots observed yet; legacy capacity may still be serving.</p> : (
        <div className="endpoint-slots-grid">
          {slots.map((slot) => (
            <article key={`${slot.container_id || "slot"}-${slot.slot}`} className="endpoint-slot-card">
              <div className="row between">
                <strong>Slot {slot.slot}</strong>
                <StatusPill value={slot.draining ? "draining" : slot.lifecycle} />
              </div>
              <dl className="endpoint-deployment-meta">
                <div><dt>Revision</dt><dd className="mono" title={slot.revision_id || ""}>{shortIdentity(slot.revision_id)}</dd></div>
                <div><dt>Build</dt><dd className="mono" title={slot.build_id || ""}>{shortIdentity(slot.build_id)}</dd></div>
                <div><dt>Ready</dt><dd>{slot.ready ? "Yes" : "No"}</dd></div>
                <div><dt>Worker</dt><dd className="mono" title={slot.worker_id || ""}>{shortIdentity(slot.worker_id)}</dd></div>
              </dl>
              {slot.failure_reason ? <p className="endpoint-deployment-reason">{safeOperatorText(slot.failure_reason)}</p> : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
