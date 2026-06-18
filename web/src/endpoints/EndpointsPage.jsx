import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

function buildApiUrl(path) {
  const base = import.meta.env.VITE_API_BASE_URL?.trim();
  if (!base) return path;
  return `${base.replace(/\/$/, "")}${path}`;
}

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

function describeEndpointWorkerState(status, taskId, endpointId) {
  if (status === "listening") return endpointId ? "Ready for assigned endpoint traffic" : "Ready";
  if (status === "idle") return "Waiting for an endpoint assignment";
  if (status === "preparing") return "Installing bundle dependencies";
  if (status === "running") return taskId ? "Processing endpoint invocation" : "Busy";
  if (status === "failed") return "Warmup or execution failed";
  return "Heartbeat reported";
}

function formatWorkerLastSeen(value) {
  if (!value) {
    return "unknown";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "unknown";
  }
  return parsed.toLocaleString();
}

function EndpointWorkersSection({ endpointWorkers, endpoints }) {
  const workers = Array.isArray(endpointWorkers) ? endpointWorkers : [];
  const totalWorkers = workers.length;
  const readyWorkers = workers.filter((worker) => worker?.status === "listening").length;
  const busyWorkers = workers.filter((worker) => worker?.status === "running").length;
  const preparingWorkers = workers.filter((worker) => worker?.status === "preparing").length;
  const failedWorkers = workers.filter((worker) => worker?.status === "failed").length;
  const endpointNameById = new Map((Array.isArray(endpoints) ? endpoints : []).map((endpoint) => [endpoint.id, endpoint.name || endpoint.id]));

  return (
    <section className="panel card-pad runs-workers-section">
      <div className="row between" style={{ gap: 12, marginBottom: 10, alignItems: "flex-start" }}>
        <div>
          <h3 className="t-h2" style={{ marginBottom: 6 }}>Endpoint workers</h3>
          <p className="muted t-sm">
            {readyWorkers} ready of {totalWorkers} total
            {busyWorkers ? ` · ${busyWorkers} busy` : ""}
            {preparingWorkers ? ` · ${preparingWorkers} preparing` : ""}
            {failedWorkers ? ` · ${failedWorkers} failed` : ""}
          </p>
        </div>
      </div>
      {!workers.length ? (
        <div className="dashboard-zero">No endpoint workers reported yet.</div>
      ) : (
        <div className="runs-workers-grid">
          {workers.map((worker) => {
            const endpointLabel = worker.endpoint_id ? (endpointNameById.get(worker.endpoint_id) || worker.endpoint_id) : "Unassigned";
            return (
              <article key={worker.worker_id} className="runs-worker-card">
                <div className="row between" style={{ gap: 10, alignItems: "center" }}>
                  <div className="col gap-1" style={{ minWidth: 0 }}>
                    <div className="mono cap" style={{ overflowWrap: "anywhere" }}>{worker.worker_id}</div>
                    <div className="muted t-xs">Last seen {formatWorkerLastSeen(worker.last_seen)}</div>
                  </div>
                  <EndpointWorkerStatusPill status={worker.status} />
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
                    <dd>{describeEndpointWorkerState(worker.status, worker.task_id, worker.endpoint_id)}</dd>
                  </div>
                  <div>
                    <dt>Assignment</dt>
                    <dd>{worker.endpoint_id ? "Pinned" : "Available"}</dd>
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
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [endpoints, setEndpoints] = useState([]);
  const [endpointWorkers, setEndpointWorkers] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [copiedEndpointId, setCopiedEndpointId] = useState("");

  const loadEndpointWorkers = async () => {
    try {
      const response = await fetch(`${apiBase}/endpoint-workers`, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Could not load endpoint workers (${response.status})`);
      }
      const payload = await response.json();
      setEndpointWorkers(Array.isArray(payload?.items) ? payload.items : []);
    } catch {
      setEndpointWorkers([]);
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
                        <Button size="sm" onClick={() => copyCurlCommand(endpoint.id, buildSyncCurlCommand(apiBase, endpoint.id, `<your-endpoint-key>`))}>{copiedEndpointId === endpoint.id ? "Copied" : "Copy curl"}</Button>
                        <Button size="sm" variant="danger" className="bundles-delete-btn" onClick={() => deleteEndpoint(endpoint.id)} disabled={deletingId === endpoint.id}>
                          {deletingId === endpoint.id ? "Deleting..." : "Delete"}
                        </Button>
                      </div>
                    </div>
                    <div className="endpoints-list-copy">
                      <span className="cap mono">Bundle {endpoint.module_bundle_name || endpoint.module_import_id || "unknown"}</span>
                      <span className="cap mono">Pinned workers {endpoint.pinned_worker_count || 1}</span>
                      <span className="cap mono">Sync POST {buildApiUrl(`/bundle-endpoints/${endpoint.id}/invoke`)}</span>
                      <span className="cap mono">SSE POST {buildApiUrl(`/bundle-endpoints/${endpoint.id}/stream`)}</span>
                      <span className="cap mono">Key preview ...{endpoint.key_preview || "unknown"}</span>
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
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
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
  const syncCurlCommand = buildSyncCurlCommand(apiBase, endpointId, apiKey);
  const streamCurlCommand = buildStreamCurlCommand(apiBase, endpointId, apiKey);

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
