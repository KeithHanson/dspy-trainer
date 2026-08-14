import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";
import { buildAbsoluteApiUrl, buildApiUrl, normalizeApiBaseUrl } from "../api/base";

async function readApiError(response, fallback) {
  try {
    const payload = await response.json();
    if (payload?.error) {
      return payload.error;
    }
  } catch {
    return fallback;
  }
  return fallback;
}

const EMPTY_FORM = {
  name: "",
  module_import_id: "",
  lm_profile_id: "",
  pinned_worker_count: "1",
};

function buildSyncCurlCommand(apiBase, endpointId, apiKey) {
  if (!endpointId || !apiKey) {
    return "Save or regenerate a key to get a ready-to-run sync curl example.";
  }
  return [
    `curl -X POST "${apiBase}/bundle-endpoints/${endpointId}/invoke"`,
    `  -H "Authorization: Bearer ${apiKey}"`,
    '  -H "Content-Type: application/json"',
    `  -d '{"question":"hello"}'`,
  ].join(" \\\n");
}

function buildStreamCurlCommand(apiBase, endpointId, apiKey) {
  if (!endpointId || !apiKey) {
    return "Save or regenerate a key to get a ready-to-run SSE curl example.";
  }
  return [
    `curl -N -X POST "${apiBase}/bundle-endpoints/${endpointId}/stream"`,
    `  -H "Authorization: Bearer ${apiKey}"`,
    '  -H "Content-Type: application/json"',
    `  -d '{"question":"hello"}'`,
  ].join(" \\\n");
}

function EndpointWorkerStatusPill({ status }) {
  const normalized = String(status || "").toLowerCase();
  const toneClass = normalized === "listening" || normalized === "idle"
    ? "runs-status-pill-pass"
    : normalized === "failed"
      ? "runs-status-pill-fail"
      : normalized === "running" || normalized === "preparing"
        ? "runs-status-pill-run"
        : "runs-status-pill-neutral";
  return <span className={`plans-status ${toneClass}`}>{status || "unknown"}</span>;
}

function describeEndpointWorkerState(status, taskId, endpointId, stateSummary) {
  if (stateSummary) return stateSummary;
  if (status === "listening") return endpointId ? "Ready for assigned endpoint traffic" : "Ready";
  if (status === "idle") return "Waiting for an endpoint assignment";
  if (status === "preparing") return "Installing bundle dependencies";
  if (status === "stale") return "Heartbeat expired";
  if (status === "running") return taskId ? "Processing endpoint invocation" : "Busy";
  if (status === "failed") return "Warmup or execution failed";
  return "Heartbeat reported";
}

function formatRevision(value) {
  return value ? String(value).slice(0, 8) : "-";
}

function formatTimestamp(value) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }
  return parsed.toLocaleString();
}

function formatDigest(value) {
  if (!value) {
    return "-";
  }
  const text = String(value);
  if (text.length <= 18) {
    return text;
  }
  return `${text.slice(0, 18)}…`;
}

function formatWorkerLastSeen(value) {
  const formatted = formatTimestamp(value);
  return formatted === "-" ? "unknown" : formatted;
}

function describeEndpointDeployment(endpoint) {
  const currentRevision = endpoint?.current_module_revision_id || null;
  const preparedRevision = endpoint?.prepared_revision_id || null;
  const deployedRevision = endpoint?.deployed_revision_id || null;
  const restartGeneration = Number(endpoint?.restart_generation ?? 0);
  if (!currentRevision) {
    return "No module revision available yet.";
  }
  if (preparedRevision !== currentRevision || !endpoint?.prepared_image_ref || !endpoint?.prepared_image_digest) {
    return `Latest bundle revision ${formatRevision(currentRevision)} needs rebuild before deploy.`;
  }
  if (deployedRevision !== currentRevision) {
    return `Prepared revision ${formatRevision(currentRevision)} is ready to deploy.`;
  }
  return `Live on revision ${formatRevision(currentRevision)} (restart gen ${restartGeneration}).`;
}

function buildEndpointWorkerSummary(endpointId, endpointWorkers) {
  const workers = Array.isArray(endpointWorkers?.items)
    ? endpointWorkers.items.filter((worker) => String(worker?.assigned_endpoint_id || "").trim() === endpointId)
    : [];
  const summary = {
    total: workers.length,
    ready: 0,
    warming: 0,
    stale: 0,
    mismatched: 0,
    failed: 0,
    running: 0,
  };
  workers.forEach((worker) => {
    if (!worker?.is_live) {
      summary.stale += 1;
    }
    if (worker?.deploy_state === "ready") {
      summary.ready += 1;
    } else if (worker?.status === "preparing") {
      summary.warming += 1;
    } else if (worker?.status === "running") {
      summary.running += 1;
    } else if (worker?.status === "failed") {
      summary.failed += 1;
    } else if (worker?.deploy_state && worker.deploy_state !== "unassigned") {
      summary.mismatched += 1;
    }
  });
  return summary;
}

function describeEndpointConvergence(endpointId, endpointWorkers, pinnedWorkerCount) {
  const summary = buildEndpointWorkerSummary(endpointId, endpointWorkers);
  if (!summary.total) {
    return `0 / ${pinnedWorkerCount || 1} assigned workers reporting`; 
  }
  const parts = [`${summary.ready}/${summary.total} ready`];
  if (summary.warming) parts.push(`${summary.warming} warming`);
  if (summary.running) parts.push(`${summary.running} running`);
  if (summary.mismatched) parts.push(`${summary.mismatched} mismatched`);
  if (summary.failed) parts.push(`${summary.failed} failed`);
  if (summary.stale) parts.push(`${summary.stale} stale`);
  parts.push(`target ${pinnedWorkerCount || 1}`);
  return parts.join(" · ");
}

function summarizeRolloutState(endpoint) {
  const rolloutState = endpoint?.rollout_state && typeof endpoint.rollout_state === "object" ? endpoint.rollout_state : {};
  const status = rolloutState.status || "idle";
  const lastAction = rolloutState.last_action ? ` after ${rolloutState.last_action}` : "";
  const updatedAt = rolloutState.updated_at ? ` · updated ${formatTimestamp(rolloutState.updated_at)}` : "";
  return `${status}${lastAction}${updatedAt}`;
}

function RolloutHistoryList({ title, items, kind }) {
  const history = Array.isArray(items) ? items.slice().reverse() : [];
  return (
    <div style={{ minWidth: 0, flex: 1 }}>
      <div className="t-label" style={{ marginBottom: 6 }}>{title}</div>
      {history.length ? (
        <div className="col gap-1">
          {history.map((item) => (
            <div key={item.id || `${kind}-${item.action}-${item.created_at || ""}`} className="panel" style={{ padding: 10 }}>
              <div className="row between" style={{ gap: 12, alignItems: "center" }}>
                <strong>{item.action || kind}</strong>
                <span className="muted t-xs">{formatTimestamp(item.completed_at || item.created_at)}</span>
              </div>
              <div className="muted t-xs" style={{ marginTop: 4 }}>
                {kind === "operation"
                  ? `${item.status || "unknown"} · restart gen ${item.restart_generation ?? "-"}`
                  : `${item.kind || "event"} · op ${formatRevision(item.operation_id)}`}
              </div>
              {item.metadata && Object.keys(item.metadata).length ? (
                <pre className="mono t-xs" style={{ marginTop: 8, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(item.metadata, null, 2)}</pre>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="dashboard-zero">No {title.toLowerCase()} yet.</div>
      )}
    </div>
  );
}

function EndpointWorkersSection({ endpointWorkers, endpoints }) {
  const workersPayload = endpointWorkers && typeof endpointWorkers === "object" ? endpointWorkers : {};
  const workers = Array.isArray(workersPayload.items) ? workersPayload.items : [];
  const totalWorkers = Number(workersPayload.total_workers ?? workers.length);
  const readyWorkers = Number(workersPayload.ready_workers ?? workers.filter((worker) => worker?.deploy_state === "ready").length);
  const liveWorkers = Number(workersPayload.live_workers ?? workers.filter((worker) => worker?.is_live).length);
  const staleWorkers = Number(workersPayload.stale_workers ?? workers.filter((worker) => !worker?.is_live).length);
  const assignedWorkers = Number(workersPayload.assigned_workers ?? workers.filter((worker) => worker?.assigned_endpoint_id).length);
  const unassignedWorkers = Number(workersPayload.unassigned_workers ?? workers.filter((worker) => !worker?.assigned_endpoint_id).length);
  const preparingWorkers = Number(workersPayload.warming_workers ?? workers.filter((worker) => worker?.status === "preparing").length);
  const runningWorkers = Number(workersPayload.running_workers ?? workers.filter((worker) => worker?.status === "running").length);
  const failedWorkers = Number(workersPayload.failed_workers ?? workers.filter((worker) => worker?.status === "failed").length);
  const endpointNameById = new Map((Array.isArray(endpoints) ? endpoints : []).map((endpoint) => [endpoint.id, endpoint.name || endpoint.id]));

  return (
    <section className="panel card-pad runs-workers-section">
      <div className="row between" style={{ gap: 12, marginBottom: 10, alignItems: "flex-start" }}>
        <div>
          <h3 className="t-h2" style={{ marginBottom: 6 }}>Endpoint workers</h3>
          <p className="muted t-sm">
            {readyWorkers} ready of {totalWorkers} total
            {liveWorkers || staleWorkers ? ` · ${liveWorkers} live · ${staleWorkers} stale` : ""}
            {assignedWorkers || unassignedWorkers ? ` · ${assignedWorkers} assigned · ${unassignedWorkers} unassigned` : ""}
            {runningWorkers ? ` · ${runningWorkers} running` : ""}
            {preparingWorkers ? ` · ${preparingWorkers} warming` : ""}
            {failedWorkers ? ` · ${failedWorkers} failed` : ""}
          </p>
        </div>
      </div>
      {!workers.length ? (
        <div className="dashboard-zero">No endpoint workers registered yet.</div>
      ) : (
        <div className="runs-workers-grid">
          {workers.map((worker) => {
            const assignedEndpointId = worker.assigned_endpoint_id || worker.endpoint_id || null;
            const endpointLabel = assignedEndpointId ? (endpointNameById.get(assignedEndpointId) || assignedEndpointId) : "Unassigned";
            return (
              <article key={worker.worker_id} className="runs-worker-card">
                <div className="row between" style={{ gap: 10, alignItems: "center" }}>
                  <div className="col gap-1" style={{ minWidth: 0 }}>
                    <div className="mono cap" style={{ overflowWrap: "anywhere" }}>{worker.worker_id}</div>
                    <div className="muted t-xs">Last seen {formatWorkerLastSeen(worker.last_seen)}</div>
                  </div>
                  <EndpointWorkerStatusPill status={worker.state_label || worker.status} />
                </div>
                <dl className="runs-worker-meta">
                  <div>
                    <dt>Endpoint</dt>
                    <dd style={{ overflowWrap: "anywhere" }}>{endpointLabel}</dd>
                  </div>
                  <div>
                    <dt>Task</dt>
                    <dd className="mono">{worker.task_id || "Idle"}</dd>
                  </div>
                  <div>
                    <dt>State</dt>
                    <dd>{describeEndpointWorkerState(worker.status, worker.task_id, assignedEndpointId, worker.state_summary)}</dd>
                  </div>
                  <div>
                    <dt>Heartbeat</dt>
                    <dd>{worker.is_live ? "Live" : "Stale"}</dd>
                  </div>
                  <div>
                    <dt>Deploy</dt>
                    <dd>{worker.deploy_state || (assignedEndpointId ? "assigned" : "unassigned")}</dd>
                  </div>
                  <div>
                    <dt>Desired rev</dt>
                    <dd className="mono">{formatRevision(worker.desired_revision_id)}</dd>
                  </div>
                  <div>
                    <dt>Warmed rev</dt>
                    <dd className="mono">{formatRevision(worker.warmed_revision_id)}</dd>
                  </div>
                  <div>
                    <dt>Desired gen</dt>
                    <dd className="mono">{worker.desired_restart_generation ?? "-"}</dd>
                  </div>
                  <div>
                    <dt>Warmed gen</dt>
                    <dd className="mono">{worker.warmed_restart_generation ?? "-"}</dd>
                  </div>
                  <div>
                    <dt>Assignment</dt>
                    <dd>{assignedEndpointId ? "Assigned" : "Unassigned"}</dd>
                  </div>
                </dl>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function EndpointsPage() {
  const navigate = useNavigate();
  const apiBase = useMemo(() => normalizeApiBaseUrl(), []);
  const publicApiBase = useMemo(() => buildAbsoluteApiUrl(""), []);
  const [endpoints, setEndpoints] = useState([]);
  const [endpointWorkers, setEndpointWorkers] = useState({ items: [], total_workers: 0 });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [actingEndpointId, setActingEndpointId] = useState("");
  const [endpointAction, setEndpointAction] = useState("");
  const [copiedEndpointId, setCopiedEndpointId] = useState("");

  const loadEndpointWorkers = async () => {
    try {
      const response = await fetch(`${apiBase}/endpoint-workers`, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Could not load endpoint workers (${response.status})`);
      }
      const payload = await response.json();
      setEndpointWorkers(payload && typeof payload === "object" ? payload : { items: [], total_workers: 0 });
    } catch {
      setEndpointWorkers({ items: [], total_workers: 0 });
    }
  };

  const loadEndpoints = async () => {
    setIsLoading(true);
    setError("");
    try {
      const endpointsResponse = await fetch(`${apiBase}/bundle-endpoints`, { method: "GET" });
      if (!endpointsResponse.ok) {
        throw new Error(`Could not load endpoints (${endpointsResponse.status})`);
      }
      const endpointsPayload = await endpointsResponse.json();
      setEndpoints(Array.isArray(endpointsPayload) ? endpointsPayload : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load endpoints");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadEndpoints();
  }, []);

  useEffect(() => {
    loadEndpointWorkers();
    const interval = setInterval(loadEndpointWorkers, 5000);
    return () => clearInterval(interval);
  }, [apiBase]);

  const deleteEndpoint = async (endpointId) => {
    setDeletingId(endpointId);
    setError("");
    try {
      const response = await fetch(`${apiBase}/bundle-endpoints/${endpointId}`, { method: "DELETE" });
      if (!response.ok) {
        throw new Error(await readApiError(response, `Could not delete endpoint (${response.status})`));
      }
      setEndpoints((current) => current.filter((endpoint) => endpoint.id !== endpointId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete endpoint");
    } finally {
      setDeletingId("");
    }
  };

  const runEndpointAction = async (endpointId, action) => {
    setActingEndpointId(endpointId);
    setEndpointAction(action);
    setError("");
    try {
      const response = await fetch(`${apiBase}/bundle-endpoints/${endpointId}/${action}`, { method: "POST" });
      if (!response.ok) {
        throw new Error(await readApiError(response, `Could not ${action} endpoint (${response.status})`));
      }
      const payload = await response.json();
      setEndpoints((current) => current.map((endpoint) => (endpoint.id === endpointId ? payload : endpoint)));
      await loadEndpointWorkers();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not ${action} endpoint`);
    } finally {
      setActingEndpointId("");
      setEndpointAction("");
    }
  };

  const copyCurlCommand = async (endpointId, command) => {
    if (!command) {
      return;
    }
    try {
      await navigator.clipboard.writeText(command);
      setCopiedEndpointId(endpointId);
      setTimeout(() => setCopiedEndpointId(""), 1200);
    } catch {
      setError("Could not copy curl command to clipboard.");
    }
  };

  return (
    <section className="page">
      <div className="page-body lm-profiles-wrap">
        <header className="row between lm-profiles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Endpoints</h1>
            <p className="muted t-sm">Manage named bundle endpoints for synchronous JSON and SSE streaming access.</p>
            <p className="muted t-xs">Rebuild prepares the latest bundle revision once; deploy then cuts traffic over to that prepared revision without redoing dependency installation when inputs are unchanged.</p>
            <p className="muted t-xs">Restart runtime forces an in-worker reload of the deployed image/revision by bumping restart generation, without restarting the whole worker container.</p>
            <p className="muted t-xs">Worker cards show readiness state plus desired and warmed bundle revisions and restart generations so rollout mismatches are visible without checking container logs.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={loadEndpoints} disabled={isLoading}>{isLoading ? "Refreshing..." : "Refresh"}</Button>
            <Button variant="primary" onClick={() => navigate("/endpoints/new")}>New</Button>
          </div>
        </header>

        {error ? <ErrorState title="Endpoint error" description={error} /> : null}
        {isLoading ? <LoadingState label="Loading endpoints..." /> : null}
        {!isLoading && !error ? (
          endpoints.length ? (
            <div className="col gap-2">
              {endpoints.map((endpoint) => (
                <article key={endpoint.id} className="bundles-saved-row lm-profiles-card">
                  <div className="bundles-saved-icon center lm-profiles-icon">
                    <span className="t-label">API</span>
                  </div>
                  <div className="bundles-row-btn lm-profiles-meta">
                    <div className="row between lm-profiles-title-row">
                      <div className="row gap-2 lm-profiles-title-group">
                        <strong>{endpoint.name || "Untitled endpoint"}</strong>
                      </div>
                      <div className="row gap-2 lm-profiles-actions">
                        <Button size="sm" onClick={() => navigate(`/endpoints/${encodeURIComponent(endpoint.id)}/edit`)}>Edit</Button>
                        <Button size="sm" onClick={() => runEndpointAction(endpoint.id, "rebuild")} disabled={actingEndpointId === endpoint.id}>{actingEndpointId === endpoint.id && endpointAction === "rebuild" ? "Rebuilding..." : "Rebuild"}</Button>
                        <Button size="sm" onClick={() => runEndpointAction(endpoint.id, "deploy")} disabled={actingEndpointId === endpoint.id || !endpoint.current_module_revision_id || endpoint.prepared_revision_id !== endpoint.current_module_revision_id || !endpoint.prepared_image_ref || !endpoint.prepared_image_digest}>{actingEndpointId === endpoint.id && endpointAction === "deploy" ? "Deploying..." : "Deploy"}</Button>
                        <Button size="sm" onClick={() => runEndpointAction(endpoint.id, "restart-runtime")} disabled={actingEndpointId === endpoint.id || !endpoint.deployed_revision_id}>{actingEndpointId === endpoint.id && endpointAction === "restart-runtime" ? "Restarting..." : "Restart runtime"}</Button>
                        <Button size="sm" onClick={() => copyCurlCommand(endpoint.id, buildSyncCurlCommand(publicApiBase, endpoint.id, `<your-endpoint-key>`))}>{copiedEndpointId === endpoint.id ? "Copied" : "Copy curl"}</Button>
                        <Button size="sm" variant="danger" className="bundles-delete-btn" onClick={() => deleteEndpoint(endpoint.id)} disabled={deletingId === endpoint.id}>
                          {deletingId === endpoint.id ? "Deleting..." : "Delete"}
                        </Button>
                      </div>
                    </div>
                    <div className="endpoints-list-copy">
                      <span className="cap mono">Bundle {endpoint.module_bundle_name || endpoint.module_import_id || "unknown"}</span>
                      <span className="cap mono">Pinned workers {endpoint.pinned_worker_count || 1}</span>
                      <span className="cap mono">Latest rev {formatRevision(endpoint.current_module_revision_id)}</span>
                      <span className="cap mono">Prepared rev {formatRevision(endpoint.prepared_revision_id)}</span>
                      <span className="cap mono">Deployed rev {formatRevision(endpoint.deployed_revision_id)}</span>
                      <span className="cap mono">Restart gen {endpoint.restart_generation ?? 0}</span>
                      <span className="cap mono">Prepared image {endpoint.prepared_image_ref || "-"}</span>
                      <span className="cap mono">Prepared image digest {formatDigest(endpoint.prepared_image_digest)}</span>
                      <span className="cap mono">Prepared at {formatTimestamp(endpoint.prepared_at)}</span>
                      <span className="cap mono">Deployed image {endpoint.deployed_image_ref || "-"}</span>
                      <span className="cap mono">Deployed image digest {formatDigest(endpoint.deployed_image_digest)}</span>
                      <span className="cap mono">Deployed at {formatTimestamp(endpoint.deployed_at)}</span>
                      <span className="cap mono">Convergence {describeEndpointConvergence(endpoint.id, endpointWorkers, endpoint.pinned_worker_count)}</span>
                      <span className="cap mono">Rollout state {summarizeRolloutState(endpoint)}</span>
                      <span className="cap mono">Sync POST {buildApiUrl(`/bundle-endpoints/${endpoint.id}/invoke`)}</span>
                      <span className="cap mono">SSE POST {buildApiUrl(`/bundle-endpoints/${endpoint.id}/stream`)}</span>
                      <span className="cap mono">Key preview ...{endpoint.key_preview || "unknown"}</span>
                    </div>
                    <p className="muted t-xs" style={{ marginTop: 8 }}>{describeEndpointDeployment(endpoint)}</p>
                    <div className="row" style={{ gap: 12, marginTop: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
                      <RolloutHistoryList title="Rollout operations" items={endpoint.rollout_operations} kind="operation" />
                      <RolloutHistoryList title="Rollout events" items={endpoint.rollout_events} kind="event" />
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No endpoints yet" description="Create an endpoint to expose a bundle over synchronous JSON or SSE streaming." />
          )
        ) : null}
        {!isLoading && !error ? <EndpointWorkersSection endpointWorkers={endpointWorkers} endpoints={endpoints} /> : null}
      </div>
    </section>
  );
}

export function EndpointEditorPage() {
  const apiBase = useMemo(() => normalizeApiBaseUrl(), []);
  const publicApiBase = useMemo(() => buildAbsoluteApiUrl(""), []);
  const location = useLocation();
  const navigate = useNavigate();
  const { endpointId } = useParams();
  const isEditing = Boolean(endpointId);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isRotating, setIsRotating] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [bundles, setBundles] = useState([]);
  const [lmProfiles, setLmProfiles] = useState([]);
  const [apiKey, setApiKey] = useState(typeof location.state?.apiKey === "string" ? location.state.apiKey : "");
  const syncCurlCommand = buildSyncCurlCommand(publicApiBase, endpointId, apiKey);
  const streamCurlCommand = buildStreamCurlCommand(publicApiBase, endpointId, apiKey);

  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      setError("");
      try {
        const [bundlesResponse, lmProfilesResponse, endpointResponse] = await Promise.all([
          fetch(`${apiBase}/modules`, { method: "GET" }),
          fetch(`${apiBase}/lm-profiles`, { method: "GET" }),
          endpointId ? fetch(`${apiBase}/bundle-endpoints/${endpointId}`, { method: "GET" }) : Promise.resolve(null),
        ]);
        if (!bundlesResponse.ok || !lmProfilesResponse.ok) {
          throw new Error(`Could not load endpoint dependencies (${bundlesResponse.status}/${lmProfilesResponse.status})`);
        }
        const bundlesPayload = await bundlesResponse.json();
        const lmProfilesPayload = await lmProfilesResponse.json();
        const nextBundles = Array.isArray(bundlesPayload) ? bundlesPayload : [];
        const nextLmProfiles = Array.isArray(lmProfilesPayload) ? lmProfilesPayload : [];
        setBundles(nextBundles);
        setLmProfiles(nextLmProfiles);
        if (endpointResponse) {
          if (!endpointResponse.ok) {
            throw new Error(await readApiError(endpointResponse, `Could not load endpoint (${endpointResponse.status})`));
          }
          const endpoint = await endpointResponse.json();
          setForm({
            name: endpoint.name || "",
            module_import_id: endpoint.module_import_id || "",
            lm_profile_id: endpoint.lm_profile_id || "",
            pinned_worker_count: String(endpoint.pinned_worker_count || 1),
          });
        } else if (nextBundles.length === 1) {
          setForm((current) => ({ ...current, module_import_id: current.module_import_id || nextBundles[0].id }));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load endpoint editor");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [apiBase, endpointId]);

  const validate = () => {
    if (!form.name.trim()) {
      return "Endpoint name is required.";
    }
    if (!form.module_import_id.trim()) {
      return "Module bundle is required.";
    }
    if (!String(form.pinned_worker_count || "").trim()) {
      return "Pinned worker count is required.";
    }
    return "";
  };

  const saveEndpoint = async () => {
    const validationError = validate();
    setFormError(validationError);
    if (validationError) {
      return;
    }
    setIsSaving(true);
    setError("");
    try {
      const payload = {
        name: form.name.trim(),
        module_import_id: form.module_import_id.trim(),
        lm_profile_id: form.lm_profile_id.trim() || null,
        pinned_worker_count: Number.parseInt(form.pinned_worker_count, 10),
      };
      const url = isEditing ? `${apiBase}/bundle-endpoints/${endpointId}` : `${apiBase}/bundle-endpoints`;
      const method = isEditing ? "PATCH" : "POST";
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await readApiError(response, `Could not save endpoint (${response.status})`));
      }
      const saved = await response.json();
      if (typeof saved.api_key === "string") {
        setApiKey(saved.api_key);
      }
      if (isEditing) {
        setForm({
          name: saved.name || payload.name,
          module_import_id: saved.module_import_id || payload.module_import_id,
          lm_profile_id: saved.lm_profile_id || payload.lm_profile_id || "",
          pinned_worker_count: String(saved.pinned_worker_count || payload.pinned_worker_count || 1),
        });
      } else {
        navigate(`/endpoints/${encodeURIComponent(saved.id)}/edit`, { state: { apiKey: saved.api_key || "" } });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save endpoint");
    } finally {
      setIsSaving(false);
    }
  };

  const rotateKey = async () => {
    if (!isEditing) {
      await saveEndpoint();
      return;
    }
    setIsRotating(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/bundle-endpoints/${endpointId}/regenerate-key`, { method: "POST" });
      if (!response.ok) {
        throw new Error(await readApiError(response, `Could not regenerate key (${response.status})`));
      }
      const payload = await response.json();
      setApiKey(typeof payload.api_key === "string" ? payload.api_key : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not regenerate key");
    } finally {
      setIsRotating(false);
    }
  };

  if (isLoading) {
    return <section className="page"><div className="page-body lm-profiles-wrap"><LoadingState label="Loading endpoint..." /></div></section>;
  }

  return (
    <section className="page">
      <div className="page-body lm-profiles-wrap">
        <header className="row between lm-profiles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>{isEditing ? "Edit endpoint" : "New endpoint"}</h1>
            <p className="muted t-sm">Set the endpoint name, pick the source bundle, and manage the external access key.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={() => navigate("/endpoints")}>Back</Button>
          </div>
        </header>

        {error ? <ErrorState title="Endpoint editor error" description={error} /> : null}
        <section className="panel card-pad bundles-section">
          <div className="bundles-metadata-grid">
            <label className="bundles-label" htmlFor="endpoint-name">Endpoint name</label>
            <input id="endpoint-name" aria-label="Endpoint name" className="bundles-file-input" type="text" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} />
            <label className="bundles-label" htmlFor="endpoint-module">Module bundle</label>
            <select id="endpoint-module" aria-label="Module bundle" className="bundles-input" value={form.module_import_id} onChange={(event) => setForm((current) => ({ ...current, module_import_id: event.target.value }))}>
              <option value="">Select a bundle</option>
              {bundles.map((bundle) => (
                <option key={bundle.id} value={bundle.id}>{bundle.bundle_name || bundle.github_repo_url || bundle.id}</option>
              ))}
            </select>
            <label className="bundles-label" htmlFor="endpoint-lm-profile">LM profile</label>
            <select id="endpoint-lm-profile" aria-label="LM profile" className="bundles-input" value={form.lm_profile_id} onChange={(event) => setForm((current) => ({ ...current, lm_profile_id: event.target.value }))}>
              <option value="">No LM profile</option>
              {lmProfiles.map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
              ))}
            </select>
            <label className="bundles-label" htmlFor="endpoint-pinned-workers">Pinned workers</label>
            <input id="endpoint-pinned-workers" aria-label="Pinned workers" className="bundles-input" type="number" min="1" step="1" value={form.pinned_worker_count} onChange={(event) => setForm((current) => ({ ...current, pinned_worker_count: event.target.value }))} />
          </div>
          <div className="bundles-endpoint-meta" style={{ marginTop: 14 }}>
            <div>
              <span className="t-label">Sync POST</span>
              <code>{endpointId ? buildApiUrl(`/bundle-endpoints/${endpointId}/invoke`) : "Created after first save"}</code>
            </div>
            <div>
              <span className="t-label">SSE POST</span>
              <code>{endpointId ? buildApiUrl(`/bundle-endpoints/${endpointId}/stream`) : "Created after first save"}</code>
            </div>
            <div>
              <span className="t-label">LM profile</span>
              <code>{lmProfiles.find((profile) => profile.id === form.lm_profile_id)?.name || (form.lm_profile_id || "No LM profile")}</code>
            </div>
            <div>
              <span className="t-label">Pinned workers</span>
              <code>{form.pinned_worker_count || "1"}</code>
            </div>
            <div>
              <span className="t-label">Current key</span>
              <code>{apiKey || (isEditing ? "Rotate to reveal a new key" : "Generate on save or via Generate key")}</code>
            </div>
          </div>
          <div className="bundles-endpoint-meta" style={{ marginTop: 14 }}>
            <div>
              <span className="t-label">Sync curl</span>
              <pre className="bundles-structure lm-profiles-curl-box">{syncCurlCommand}</pre>
            </div>
            <div>
              <span className="t-label">SSE curl</span>
              <pre className="bundles-structure lm-profiles-curl-box">{streamCurlCommand}</pre>
            </div>
          </div>
          {formError ? <p className="cap" style={{ marginTop: 8 }}>{formError}</p> : null}
          <div className="row gap-2" style={{ marginTop: 14 }}>
            <Button onClick={rotateKey} disabled={isRotating || isSaving}>{isRotating ? "Generating..." : (isEditing ? "Regenerate key" : "Generate key")}</Button>
            <Button variant="primary" onClick={saveEndpoint} disabled={isSaving}>{isSaving ? "Saving..." : "Save endpoint"}</Button>
          </div>
        </section>
      </div>
    </section>
  );
}
