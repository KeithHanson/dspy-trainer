import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const EMPTY_FORM = {
  name: "",
  model: "openai/gpt-4o-mini",
  api_base: "",
  model_type: "responses",
  default_params: JSON.stringify({ temperature: 0.2, max_tokens: 2048 }, null, 2),
  lm_class_path: "",
  upstream_api_key: "",
};

const MODEL_TYPE_OPTIONS = ["responses", "chat", "text"];

export function LmProfilesPage() {
  const navigate = useNavigate();
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [profiles, setProfiles] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [visibleKeyIds, setVisibleKeyIds] = useState({});
  const [copiedKeyId, setCopiedKeyId] = useState("");

  const loadProfiles = async () => {
    setIsLoading(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/lm-profiles`, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Could not load LM profiles (${response.status})`);
      }
      const payload = await response.json();
      setProfiles(Array.isArray(payload) ? payload : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load LM profiles");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadProfiles();
  }, []);

  const deleteProfile = async (profileId) => {
    setDeletingId(profileId);
    setError("");
    try {
      const response = await fetch(`${apiBase}/lm-profiles/${profileId}`, { method: "DELETE" });
      if (!response.ok) {
        throw new Error(`Could not delete LM profile (${response.status})`);
      }
      setProfiles((prev) => prev.filter((item) => item.id !== profileId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete LM profile");
    } finally {
      setDeletingId("");
    }
  };

  const toggleKeyVisibility = (profileId) => {
    setVisibleKeyIds((prev) => ({ ...prev, [profileId]: !prev[profileId] }));
  };

  const copyVirtualKey = async (profileId, keyValue) => {
    if (!keyValue) {
      return;
    }
    try {
      await navigator.clipboard.writeText(keyValue);
      setCopiedKeyId(profileId);
      setTimeout(() => setCopiedKeyId(""), 1200);
    } catch {
      setError("Could not copy virtual key to clipboard.");
    }
  };

  return (
    <section className="page">
      <div className="page-body lm-profiles-wrap">
        <header className="row between lm-profiles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>LM Profiles</h1>
            <p className="muted t-sm">Manage reusable model config for plan-level run execution.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={loadProfiles} disabled={isLoading}>{isLoading ? "Refreshing..." : "Refresh"}</Button>
            <Button variant="primary" onClick={() => navigate("/lm-profiles/new")}>New profile</Button>
          </div>
        </header>

        {error ? <ErrorState title="LM profile error" description={error} /> : null}
        {isLoading ? <LoadingState label="Loading LM profiles..." /> : null}
        {!isLoading && !error ? (
          profiles.length ? (
            <div className="col gap-2">
              {profiles.map((profile) => (
                <article key={profile.id} className="panel card-pad lm-profiles-row">
                  <div className="col gap-1" style={{ flex: 1 }}>
                    <div className="row gap-2">
                      <strong>{profile.name || "Untitled profile"}</strong>
                      <span className="cap mono">{profile.model_type || "type n/a"}</span>
                    </div>
                    <span className="cap mono">{profile.model || "model n/a"} · {profile.api_base || "api base n/a"}</span>
                    <span className="cap mono">{profile.lm_class_path || "dspy.LM (default)"} · updated {formatDate(profile.updated_at)}</span>
                    <div className="row gap-2" style={{ marginTop: 4 }}>
                      <span className="cap mono">
                        Virtual key: {profile.virtual_key ? (visibleKeyIds[profile.id] ? profile.virtual_key : maskKey(profile.virtual_key)) : "none"}
                      </span>
                      {profile.virtual_key ? (
                        <>
                          <Button size="sm" onClick={() => toggleKeyVisibility(profile.id)}>{visibleKeyIds[profile.id] ? "🙈" : "👁"}</Button>
                          <Button size="sm" onClick={() => copyVirtualKey(profile.id, profile.virtual_key)}>{copiedKeyId === profile.id ? "✅" : "📋"}</Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                  <div className="row gap-2">
                    <Button size="sm" onClick={() => navigate(`/lm-profiles/${encodeURIComponent(profile.id)}/edit`)}>Edit</Button>
                    <Button size="sm" variant="danger" onClick={() => deleteProfile(profile.id)} disabled={deletingId === profile.id}>
                      {deletingId === profile.id ? "Deleting..." : "Delete"}
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No LM profiles" description="Create a profile to attach model settings to evaluation plans." />
          )
        ) : null}
      </div>
    </section>
  );
}

function maskKey(value) {
  if (!value || value.length < 8) {
    return "********";
  }
  return `${value.slice(0, 4)}${"*".repeat(Math.max(4, value.length - 7))}${value.slice(-3)}`;
}

export function LmProfileEditorPage() {
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const navigate = useNavigate();
  const { profileId } = useParams();
  const isEditing = Boolean(profileId);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [virtualKey, setVirtualKey] = useState("");
  const [isRotating, setIsRotating] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [testStatus, setTestStatus] = useState("");
  const [testLog, setTestLog] = useState("");

  useEffect(() => {
    const loadProfile = async () => {
      if (!profileId) {
        setForm(EMPTY_FORM);
        return;
      }
      setIsLoading(true);
      setError("");
      try {
        const response = await fetch(`${apiBase}/lm-profiles/${profileId}`, { method: "GET" });
        if (!response.ok) {
          throw new Error(`Could not load LM profile (${response.status})`);
        }
        const profile = await response.json();
        setForm({
          name: profile.name || "",
          model: profile.model || "",
          api_base: profile.api_base || "",
          model_type: profile.model_type || "responses",
          default_params: JSON.stringify(profile.default_params || {}, null, 2),
          lm_class_path: profile.lm_class_path || "",
          upstream_api_key: "",
        });
        setVirtualKey(typeof profile.virtual_key === "string" ? profile.virtual_key : "");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load LM profile");
      } finally {
        setIsLoading(false);
      }
    };
    loadProfile();
  }, [apiBase, profileId]);

  const saveProfile = async () => {
    setFormError("");
    if (!isEditing && !form.upstream_api_key.trim()) {
      setFormError("Upstream API key is required when creating a profile.");
      return;
    }
    if (!form.name.trim() || !form.model.trim() || !form.api_base.trim() || !form.model_type.trim()) {
      setFormError("Name, model, API base, and model type are required.");
      return;
    }

    let parsedParams = {};
    try {
      const raw = form.default_params.trim() || "{}";
      parsedParams = JSON.parse(raw);
      if (!parsedParams || Array.isArray(parsedParams) || typeof parsedParams !== "object") {
        throw new Error("default_params must be a JSON object");
      }
    } catch {
      setFormError("default_params must be valid JSON object syntax.");
      return;
    }

    setIsSaving(true);
    setError("");
    try {
      const payload = {
        name: form.name.trim(),
        model: form.model.trim(),
        api_base: form.api_base.trim(),
        model_type: form.model_type.trim(),
        default_params: parsedParams,
        lm_class_path: form.lm_class_path.trim() || null,
        upstream_api_key: form.upstream_api_key.trim() || null,
      };
      const url = profileId ? `${apiBase}/lm-profiles/${profileId}` : `${apiBase}/lm-profiles`;
      const method = profileId ? "PATCH" : "POST";
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const detail = await readApiError(response);
        throw new Error(detail || `Could not save LM profile (${response.status})`);
      }
      const responsePayload = await response.json();
      if (profileId) {
        setVirtualKey(typeof responsePayload.virtual_key === "string" ? responsePayload.virtual_key : "");
      } else {
        navigate("/lm-profiles");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save LM profile");
    } finally {
      setIsSaving(false);
    }
  };

  const rotateVirtualKey = async () => {
    if (!profileId) {
      return;
    }
    setIsRotating(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/lm-profiles/${profileId}/rotate-key`, { method: "POST" });
      if (!response.ok) {
        const detail = await readApiError(response);
        throw new Error(detail || `Could not rotate key (${response.status})`);
      }
      const payload = await response.json();
      setVirtualKey(typeof payload.virtual_key === "string" ? payload.virtual_key : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rotate key");
    } finally {
      setIsRotating(false);
    }
  };

  const testConnection = async () => {
    if (!profileId) {
      return;
    }
    setIsTesting(true);
    setError("");
    setTestStatus("Running connection test...");
    try {
      const response = await fetch(`${apiBase}/lm-profiles/${profileId}/test-connection`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof body.error === "string" ? body.error : `Could not test connection (${response.status})`;
        setTestStatus("Connection failed");
        setTestLog((prev) => `${prev}${prev ? "\n\n" : ""}[FAIL] ${new Date().toISOString()}\n${detail}`);
        return;
      }
      setTestStatus("Connection succeeded");
      setTestLog((prev) => `${prev}${prev ? "\n\n" : ""}[OK] ${new Date().toISOString()}\n${JSON.stringify(body, null, 2)}`);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Unknown connection error";
      setTestStatus("Connection failed");
      setTestLog((prev) => `${prev}${prev ? "\n\n" : ""}[FAIL] ${new Date().toISOString()}\n${detail}`);
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <section className="page">
      <div className="page-body lm-profiles-wrap">
        <header className="row between lm-profiles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>{isEditing ? "Edit LM Profile" : "New LM Profile"}</h1>
            <p className="muted t-sm">Configure model runtime settings for plan-level execution.</p>
          </div>
          <Link className="lnk" to="/lm-profiles">Back to LM profiles</Link>
        </header>

        <section className="panel card-pad lm-profiles-form">
          {isLoading ? <LoadingState label="Loading LM profile..." /> : null}
          {error ? <ErrorState title="LM profile error" description={error} /> : null}
          <div className="lm-profiles-grid">
            <label className="col gap-1">
              <span className="t-label">Name</span>
              <input aria-label="Name" className="bundles-input" value={form.name} onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))} />
            </label>
            <label className="col gap-1">
              <span className="t-label">Model</span>
              <input aria-label="Model" className="bundles-input" value={form.model} onChange={(event) => setForm((prev) => ({ ...prev, model: event.target.value }))} />
            </label>
            <label className="col gap-1">
              <span className="t-label">API base</span>
              <input aria-label="API base" className="bundles-input" value={form.api_base} onChange={(event) => setForm((prev) => ({ ...prev, api_base: event.target.value }))} />
            </label>
            <label className="col gap-1">
              <span className="t-label">Model type</span>
              <select aria-label="Model type" className="bundles-input" value={form.model_type} onChange={(event) => setForm((prev) => ({ ...prev, model_type: event.target.value }))}>
                {MODEL_TYPE_OPTIONS.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            </label>
            <label className="col gap-1">
              <span className="t-label">LM class path (optional)</span>
              <input aria-label="LM class path (optional)" className="bundles-input" value={form.lm_class_path} onChange={(event) => setForm((prev) => ({ ...prev, lm_class_path: event.target.value }))} />
            </label>
            <label className="col gap-1">
              <span className="t-label">Upstream API key (sent to LiteLLM only)</span>
              <input type="password" aria-label="Upstream API key (sent to LiteLLM only)" className="bundles-input" value={form.upstream_api_key} onChange={(event) => setForm((prev) => ({ ...prev, upstream_api_key: event.target.value }))} placeholder="sk-..." autoComplete="off" />
            </label>
            <label className="col gap-1">
              <span className="t-label">Default params (JSON object)</span>
              <textarea aria-label="Default params (JSON object)" className="plans-textarea" rows={4} value={form.default_params} onChange={(event) => setForm((prev) => ({ ...prev, default_params: event.target.value }))} />
            </label>
          </div>
          {formError ? <p className="lm-profiles-form-error">{formError}</p> : null}
          {isEditing ? (
            <div className="panel card-pad" style={{ marginTop: 12 }}>
              <div className="row between" style={{ marginBottom: 8 }}>
                <span className="t-label">Virtual key</span>
                <div className="row gap-2">
                  <Button size="sm" onClick={testConnection} disabled={isTesting}>{isTesting ? "Testing..." : "Test connection"}</Button>
                  <Button size="sm" onClick={rotateVirtualKey} disabled={isRotating}>{isRotating ? "Rotating..." : "Rotate key"}</Button>
                </div>
              </div>
              <div className="cap mono">{virtualKey || "No key available yet"}</div>
              <div className="cap" style={{ marginTop: 8 }}>{testStatus || "No connection test run yet."}</div>
              <pre className="bundles-structure" style={{ marginTop: 8, maxHeight: 220 }}>{testLog || "Connection test logs will appear here."}</pre>
            </div>
          ) : null}
          <div className="row gap-2" style={{ marginTop: 12 }}>
            <Button variant="primary" onClick={saveProfile} disabled={isSaving}>{isSaving ? "Saving..." : "Save profile"}</Button>
            <Button onClick={() => navigate("/lm-profiles")}>Cancel</Button>
          </div>
        </section>
      </div>
    </section>
  );
}

async function readApiError(response) {
  try {
    const payload = await response.json();
    if (payload && typeof payload.error === "string" && payload.error.trim()) {
      return payload.error;
    }
  } catch {
    return "";
  }
  return "";
}

function formatDate(value) {
  if (!value) {
    return "unknown";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "unknown";
  }
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
