import { useEffect, useMemo, useState } from "react";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const DEFAULT_FORM = {
  key_alias: "",
  models: "",
  metadata: '{"owner":"team"}',
  duration: "30d",
};

export function LiteLLMKeysPage() {
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [keys, setKeys] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [updatingKey, setUpdatingKey] = useState("");
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState(DEFAULT_FORM);
  const [editingKey, setEditingKey] = useState("");
  const [editForm, setEditForm] = useState({ metadata: "{}", models: "", alias_default: "", rpm_limit: "", tpm_limit: "", max_budget: "" });

  const loadData = async () => {
    setIsLoading(true);
    setError("");
    try {
      const [keysResp, profilesResp] = await Promise.all([
        fetch(`${apiBase}/litellm/keys`, { method: "GET" }),
        fetch(`${apiBase}/lm-profiles`, { method: "GET" }),
      ]);
      if (!keysResp.ok) {
        throw new Error(`Could not load LiteLLM keys (${keysResp.status})`);
      }
      const keyPayload = await keysResp.json();
      setKeys(Array.isArray(keyPayload?.keys) ? keyPayload.keys : []);

      if (profilesResp.ok) {
        const profilePayload = await profilesResp.json();
        setProfiles(Array.isArray(profilePayload) ? profilePayload : []);
      } else {
        setProfiles([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load LiteLLM keys");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const createKey = async () => {
    setFormError("");
    let metadata = {};
    try {
      metadata = JSON.parse(form.metadata.trim() || "{}");
      if (!metadata || Array.isArray(metadata) || typeof metadata !== "object") {
        throw new Error("metadata must be object");
      }
    } catch {
      setFormError("Metadata must be valid JSON object syntax.");
      return;
    }

    const manualModels = form.models
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const profile = profiles.find((item) => item.id === selectedProfileId);
    const mergedModels = Array.from(new Set([...(profile?.model ? [profile.model] : []), ...manualModels]));
    if (!mergedModels.length) {
      setFormError("Add at least one model or choose an LM profile.");
      return;
    }

    setIsSaving(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/litellm/keys`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          models: mergedModels,
          aliases: mergedModels[0] ? { default: mergedModels[0] } : {},
          metadata,
          duration: form.duration.trim() || null,
          key_alias: form.key_alias.trim() || null,
        }),
      });
      if (!response.ok) {
        throw new Error(`Could not create LiteLLM key (${response.status})`);
      }
      setForm(DEFAULT_FORM);
      setSelectedProfileId("");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create LiteLLM key");
    } finally {
      setIsSaving(false);
    }
  };

  const toggleBlocked = async (key, blocked) => {
    setUpdatingKey(key);
    setError("");
    try {
      const endpoint = blocked ? "restore" : "revoke";
      const response = await fetch(`${apiBase}/litellm/keys/${encodeURIComponent(key)}/${endpoint}`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Could not ${endpoint} key (${response.status})`);
      }
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update key status");
    } finally {
      setUpdatingKey("");
    }
  };

  const startEdit = (row) => {
    setEditingKey(row.key);
    setEditForm({
      metadata: JSON.stringify(row.metadata || {}, null, 2),
      models: Array.isArray(row.models) ? row.models.join(", ") : "",
      alias_default: row.aliases?.default || "",
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : "",
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : "",
      max_budget: row.max_budget != null ? String(row.max_budget) : "",
    });
    setFormError("");
  };

  const saveEdit = async () => {
    if (!editingKey) {
      return;
    }
    setFormError("");
    let metadata = {};
    try {
      metadata = JSON.parse(editForm.metadata.trim() || "{}");
      if (!metadata || Array.isArray(metadata) || typeof metadata !== "object") {
        throw new Error("metadata must be object");
      }
    } catch {
      setFormError("Metadata must be valid JSON object syntax.");
      return;
    }

    const models = editForm.models
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

    const payload = {
      key: editingKey,
      models: models.length ? models : null,
      aliases: editForm.alias_default.trim() ? { default: editForm.alias_default.trim() } : null,
      metadata,
      rpm_limit: editForm.rpm_limit.trim() ? Number(editForm.rpm_limit) : null,
      tpm_limit: editForm.tpm_limit.trim() ? Number(editForm.tpm_limit) : null,
      max_budget: editForm.max_budget.trim() ? Number(editForm.max_budget) : null,
    };

    setUpdatingKey(editingKey);
    setError("");
    try {
      const response = await fetch(`${apiBase}/litellm/keys/${encodeURIComponent(editingKey)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(`Could not update key (${response.status})`);
      }
      setEditingKey("");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update key");
    } finally {
      setUpdatingKey("");
    }
  };

  return (
    <section className="page">
      <div className="page-body lm-profiles-wrap">
        <header className="row between lm-profiles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>LiteLLM Keys</h1>
            <p className="muted t-sm">Create and manage proxy keys; secrets may be unreadable after creation.</p>
          </div>
          <Button onClick={loadData} disabled={isLoading}>{isLoading ? "Refreshing..." : "Refresh"}</Button>
        </header>

        <section className="panel card-pad lm-profiles-form">
          <h2 className="t-h2" style={{ marginBottom: 12 }}>Create key</h2>
          <div className="lm-profiles-grid">
            <label className="col gap-1">
              <span className="t-label">Seed from LM profile (optional)</span>
              <select className="bundles-input" aria-label="Seed from LM profile (optional)" value={selectedProfileId} onChange={(event) => setSelectedProfileId(event.target.value)}>
                <option value="">None</option>
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
                ))}
              </select>
            </label>
            <label className="col gap-1">
              <span className="t-label">Key alias (optional)</span>
              <input className="bundles-input" aria-label="Key alias (optional)" value={form.key_alias} onChange={(event) => setForm((prev) => ({ ...prev, key_alias: event.target.value }))} />
            </label>
            <label className="col gap-1">
              <span className="t-label">Models (comma-separated)</span>
              <input className="bundles-input" aria-label="Models (comma-separated)" placeholder="openai/gpt-4o-mini, openai/o3" value={form.models} onChange={(event) => setForm((prev) => ({ ...prev, models: event.target.value }))} />
            </label>
            <label className="col gap-1">
              <span className="t-label">Duration</span>
              <input className="bundles-input" aria-label="Duration" value={form.duration} onChange={(event) => setForm((prev) => ({ ...prev, duration: event.target.value }))} />
            </label>
            <label className="col gap-1" style={{ gridColumn: "1 / -1" }}>
              <span className="t-label">Metadata (JSON object)</span>
              <textarea className="plans-textarea" aria-label="Metadata (JSON object)" rows={4} value={form.metadata} onChange={(event) => setForm((prev) => ({ ...prev, metadata: event.target.value }))} />
            </label>
          </div>
          {formError ? <p className="lm-profiles-form-error">{formError}</p> : null}
          <div className="row gap-2" style={{ marginTop: 12 }}>
            <Button variant="primary" onClick={createKey} disabled={isSaving}>{isSaving ? "Creating..." : "Create key"}</Button>
          </div>
        </section>

        {error ? <ErrorState title="LiteLLM key error" description={error} /> : null}
        {isLoading ? <LoadingState label="Loading keys..." /> : null}
        {!isLoading && !error ? (
          keys.length ? (
            <div className="col gap-2">
              {keys.map((row) => (
                <article key={row.key} className="panel card-pad lm-profiles-row">
                  <div className="col gap-1" style={{ flex: 1 }}>
                    <div className="row gap-2">
                      <strong>{row.key_alias || row.key}</strong>
                      <span className="cap mono">{row.blocked ? "revoked" : "active"}</span>
                    </div>
                    <span className="cap mono">models: {Array.isArray(row.models) && row.models.length ? row.models.join(", ") : "none"}</span>
                    <span className="cap mono">duration: {row.duration || "n/a"}</span>
                  </div>
                  <div className="row gap-2">
                    <Button size="sm" onClick={() => startEdit(row)}>Edit</Button>
                    <Button
                      size="sm"
                      variant={row.blocked ? "default" : "danger"}
                      onClick={() => toggleBlocked(row.key, Boolean(row.blocked))}
                      disabled={updatingKey === row.key}
                    >
                      {updatingKey === row.key ? "Updating..." : row.blocked ? "Restore" : "Revoke"}
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No LiteLLM keys" description="Create a key to allow runtime access through your LiteLLM proxy." />
          )
        ) : null}

        {editingKey ? (
          <section className="panel card-pad lm-profiles-form" style={{ marginTop: 14 }}>
            <div className="row between" style={{ marginBottom: 10 }}>
              <h2 className="t-h2">Edit key</h2>
              <span className="cap mono">{editingKey}</span>
            </div>
            <div className="lm-profiles-grid">
              <label className="col gap-1" style={{ gridColumn: "1 / -1" }}>
                <span className="t-label">Metadata (JSON object)</span>
                <textarea className="plans-textarea" aria-label="Edit metadata (JSON object)" rows={4} value={editForm.metadata} onChange={(event) => setEditForm((prev) => ({ ...prev, metadata: event.target.value }))} />
              </label>
              <label className="col gap-1">
                <span className="t-label">Models (comma-separated)</span>
                <input className="bundles-input" aria-label="Edit models (comma-separated)" value={editForm.models} onChange={(event) => setEditForm((prev) => ({ ...prev, models: event.target.value }))} />
              </label>
              <label className="col gap-1">
                <span className="t-label">Default alias model</span>
                <input className="bundles-input" aria-label="Default alias model" value={editForm.alias_default} onChange={(event) => setEditForm((prev) => ({ ...prev, alias_default: event.target.value }))} />
              </label>
              <label className="col gap-1">
                <span className="t-label">RPM limit</span>
                <input className="bundles-input" aria-label="RPM limit" value={editForm.rpm_limit} onChange={(event) => setEditForm((prev) => ({ ...prev, rpm_limit: event.target.value }))} />
              </label>
              <label className="col gap-1">
                <span className="t-label">TPM limit</span>
                <input className="bundles-input" aria-label="TPM limit" value={editForm.tpm_limit} onChange={(event) => setEditForm((prev) => ({ ...prev, tpm_limit: event.target.value }))} />
              </label>
              <label className="col gap-1">
                <span className="t-label">Max budget</span>
                <input className="bundles-input" aria-label="Max budget" value={editForm.max_budget} onChange={(event) => setEditForm((prev) => ({ ...prev, max_budget: event.target.value }))} />
              </label>
            </div>
            {formError ? <p className="lm-profiles-form-error">{formError}</p> : null}
            <div className="row gap-2" style={{ marginTop: 12 }}>
              <Button variant="primary" onClick={saveEdit} disabled={updatingKey === editingKey}>{updatingKey === editingKey ? "Saving..." : "Save changes"}</Button>
              <Button onClick={() => setEditingKey("")}>Cancel</Button>
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
