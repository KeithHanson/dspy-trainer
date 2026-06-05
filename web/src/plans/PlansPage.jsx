import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const PROJECT_ID = "proj-1";
const SCENARIO_ID = "scn-1";
const DATASET_VERSION = "v1";

function getBundleDisplayName(bundle) {
  return bundle?.bundle_name || bundle?.id || "Untitled bundle";
}

function formatCreatedAt(value) {
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

async function parseApiError(response, fallback) {
  try {
    const body = await response.json();
    if (body?.error) {
      return `${fallback}: ${body.error}`;
    }
  } catch {
    return fallback;
  }
  return fallback;
}

export function PlansPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const showBuilder = searchParams.get("new") === "1";
  const editingPlanId = searchParams.get("id") || "";
  const savedFlag = searchParams.get("saved") === "1";

  return showBuilder ? (
    <PlanBuilder
      planId={editingPlanId}
      onBack={(opts) => {
        if (opts?.runPlanId) {
          navigate(`/runs?plan=${encodeURIComponent(opts.runPlanId)}`);
          return;
        }
        navigate(opts?.saved ? "/plans?saved=1" : "/plans");
      }}
    />
  ) : (
    <PlansList
      onCreate={() => navigate("/plans?new=1")}
      onEdit={(id) => navigate(`/plans?new=1&id=${encodeURIComponent(id)}`)}
      onRunNavigate={(id) => navigate(`/runs?plan=${encodeURIComponent(id)}`)}
      showSavedNotice={savedFlag}
    />
  );
}

function PlansList({ onCreate, onEdit, onRunNavigate, showSavedNotice }) {
  const plansUrl = useMemo(() => `${(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}/evaluation-plans`, []);
  const [plans, setPlans] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingPlanId, setDeletingPlanId] = useState("");
  const [runningPlanId, setRunningPlanId] = useState("");
  const [profileNames, setProfileNames] = useState({});

  const loadPlans = async () => {
    setIsLoading(true);
    setError("");
    try {
      const response = await fetch(plansUrl, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Could not load plans (${response.status})`);
      }
      const payload = await response.json();
      setPlans(Array.isArray(payload) ? payload : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load plans");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadPlans();
  }, []);

  useEffect(() => {
    const loadProfileNames = async () => {
      try {
        const response = await fetch(`${(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}/lm-profiles`, { method: "GET" });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (!Array.isArray(payload)) {
          return;
        }
        const next = {};
        payload.forEach((profile) => {
          if (profile?.id) {
            next[profile.id] = profile.name || profile.id;
          }
        });
        setProfileNames(next);
      } catch {
        setProfileNames({});
      }
    };
    loadProfileNames();
  }, []);

  const runPlan = async (plan) => {
    setRunningPlanId(plan.id);
    setError("");
    try {
      if (!plan.module_import_id) {
        throw new Error("This plan has no saved module bundle. Edit the plan and select a bundle before running.");
      }
      const moduleResp = await fetch(`${(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}/modules/${plan.module_import_id}`, { method: "GET" });
      if (!moduleResp.ok) {
        throw new Error("Could not load the module bundle for this plan. Re-open the plan and reselect a bundle, then retry.");
      }
      const modulePayload = await moduleResp.json();
      const bundlePath = modulePayload?.source_ref;
      if (!bundlePath || typeof bundlePath !== "string") {
        throw new Error("This plan's module bundle has no runnable source path. Re-upload the bundle and update the plan.");
      }
      const createRunResp = await fetch(`${(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}/agent-run-plans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: PROJECT_ID,
          module_import_id: plan.module_import_id,
          scenario_id: plan.scenario_id || SCENARIO_ID,
          dataset_version: plan.dataset_version || DATASET_VERSION,
          bundle_path: bundlePath,
          eval_inputs: [],
          evaluation_plan_id: plan.id,
          runs_per_question: plan.runs_per_question || 1,
          max_workers: plan.max_workers || 1,
        }),
      });
      if (!createRunResp.ok) {
        throw new Error(`Could not start run (${createRunResp.status}). Please retry.`);
      }
      const runPlanPayload = await createRunResp.json();
      const enqueueResp = await fetch(`${(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}/agent-run-plans/${runPlanPayload.id}/enqueue`, { method: "POST" });
      if (!enqueueResp.ok) {
        throw new Error(`Run was created but could not be queued (${enqueueResp.status}). Please retry.`);
      }
      onRunNavigate(runPlanPayload.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start run");
    } finally {
      setRunningPlanId("");
    }
  };

  const deletePlan = async (planId) => {
    setDeletingPlanId(planId);
    setError("");
    try {
      const response = await fetch(`${plansUrl}/${planId}`, { method: "DELETE" });
      if (!response.ok) {
        throw new Error(`Could not delete plan (${response.status})`);
      }
      setPlans((prev) => prev.filter((item) => item.id !== planId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete plan");
    } finally {
      setDeletingPlanId("");
    }
  };

  return (
    <section className="page">
      <div className="page-body plans-wrap">
        <header className="row between plans-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Evaluation Plans</h1>
            <p className="muted t-sm">Saved run configuration for a bundle, dataset, LM profile, and execution settings.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={loadPlans} disabled={isLoading}>{isLoading ? "Refreshing..." : "Refresh"}</Button>
            <Button variant="primary" onClick={onCreate}>New plan</Button>
          </div>
        </header>

        {isLoading ? <LoadingState label="Loading plans..." /> : null}
        {error ? <ErrorState title="Could not load plans" description={error} /> : null}
        {showSavedNotice ? <div className="plans-saved-banner" role="status">Plan saved.</div> : null}

        {!isLoading && !error ? (
          plans.length ? (
            <div className="col gap-2">
              {plans.map((plan) => (
                <article key={plan.id} className="panel card-pad plans-row">
                  <div className="plans-row-icon center">
                    <span className="plans-count">{plan.dataset_id ? 1 : 0}</span>
                  </div>
                  <div className="col gap-1" style={{ flex: 1 }}>
                    <div className="row gap-2">
                      <button type="button" className="plans-name-link" onClick={() => onEdit(plan.id)}>{plan.name || "Untitled plan"}</button>
                    </div>
                    <span className="cap mono">Dataset: {plan.dataset_name || plan.dataset_id || "none"}</span>
                    <span className="cap mono">{plan.runs_per_question || 1} runs per input · {plan.max_workers || 1} workers</span>
                    <span className="cap mono">LM profile: {plan.lm_profile_id ? (profileNames[plan.lm_profile_id] || plan.lm_profile_id) : "none"}</span>
                  </div>
                  <div className="row gap-2">
                    <Button size="sm" variant="primary" icon="activity" onClick={(event) => {
                      event.stopPropagation();
                      runPlan(plan);
                    }} disabled={runningPlanId === plan.id}>
                      {runningPlanId === plan.id ? "Starting..." : "Run"}
                    </Button>
                    <Button size="sm" onClick={(event) => {
                      event.stopPropagation();
                      onEdit(plan.id);
                    }}>
                      Edit
                    </Button>
                    <Button size="sm" variant="danger" onClick={(event) => {
                      event.stopPropagation();
                      deletePlan(plan.id);
                    }} disabled={deletingPlanId === plan.id}>
                      {deletingPlanId === plan.id ? "Deleting..." : "Delete"}
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No plans yet" description="Create your first evaluation plan from a validated module bundle and dataset." />
          )
        ) : null}
      </div>
    </section>
  );
}

function PlanBuilder({ onBack, planId }) {
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [modules, setModules] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [lmProfiles, setLmProfiles] = useState([]);
  const [isLoadingModules, setIsLoadingModules] = useState(false);
  const [isLoadingDatasets, setIsLoadingDatasets] = useState(false);
  const [isLoadingPlan, setIsLoadingPlan] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [validationError, setValidationError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [name, setName] = useState("");
  const [runs, setRuns] = useState(3);
  const [workers, setWorkers] = useState(8);
  const [selectedBundleId, setSelectedBundleId] = useState("");
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [selectedLmProfileId, setSelectedLmProfileId] = useState("");
  const isEditing = Boolean(planId);

  const validModules = useMemo(() => modules.filter((item) => item.validation_status === "passed"), [modules]);
  const selectedBundle = useMemo(() => validModules.find((item) => item.id === selectedBundleId) || null, [selectedBundleId, validModules]);
  const bundleDatasets = useMemo(() => datasets.filter((item) => item.module_import_id === selectedBundleId), [datasets, selectedBundleId]);
  const selectedDataset = useMemo(() => bundleDatasets.find((item) => item.id === selectedDatasetId) || null, [bundleDatasets, selectedDatasetId]);
  const totalTasks = (selectedDataset?.record_count || 0) * runs;

  useEffect(() => {
    const loadModules = async () => {
      setIsLoadingModules(true);
      setLoadError("");
      try {
        const response = await fetch(`${apiBase}/modules`, { method: "GET" });
        if (!response.ok) {
          throw new Error(`Could not load bundles (${response.status})`);
        }
        const payload = await response.json();
        const moduleRows = Array.isArray(payload) ? payload : [];
        setModules(moduleRows);
        if (!selectedBundleId && moduleRows.length) {
          const firstValid = moduleRows.find((item) => item.validation_status === "passed");
          if (firstValid) {
            setSelectedBundleId(firstValid.id);
          }
        }
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : "Could not load bundles");
      } finally {
        setIsLoadingModules(false);
      }
    };

    const loadDatasets = async () => {
      setIsLoadingDatasets(true);
      try {
        const response = await fetch(`${apiBase}/evaluation-datasets`, { method: "GET" });
        if (!response.ok) {
          throw new Error(`Could not load datasets (${response.status})`);
        }
        const payload = await response.json();
        setDatasets(Array.isArray(payload) ? payload : []);
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : "Could not load datasets");
      } finally {
        setIsLoadingDatasets(false);
      }
    };

    const loadLmProfiles = async () => {
      try {
        const response = await fetch(`${apiBase}/lm-profiles`, { method: "GET" });
        if (!response.ok) {
          throw new Error("Could not load LM profiles");
        }
        const payload = await response.json();
        setLmProfiles(Array.isArray(payload) ? payload : []);
      } catch {
        setLmProfiles([]);
      }
    };

    loadModules();
    loadDatasets();
    loadLmProfiles();
  }, [apiBase]);

  useEffect(() => {
    const loadPlan = async () => {
      if (!planId) {
        return;
      }
      setIsLoadingPlan(true);
      setSubmitError("");
      try {
        const response = await fetch(`${apiBase}/evaluation-plans/${planId}`, { method: "GET" });
        if (!response.ok) {
          throw new Error(`Could not load plan (${response.status})`);
        }
        const payload = await response.json();
        setName(payload.name || "");
        setRuns(Math.max(1, Number(payload.runs_per_question || 1)));
        setWorkers(Math.max(1, Number(payload.max_workers || 1)));
        setSelectedBundleId(typeof payload.module_import_id === "string" ? payload.module_import_id : "");
        setSelectedDatasetId(typeof payload.dataset_id === "string" ? payload.dataset_id : "");
        setSelectedLmProfileId(typeof payload.lm_profile_id === "string" ? payload.lm_profile_id : "");
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : "Could not load plan");
      } finally {
        setIsLoadingPlan(false);
      }
    };
    loadPlan();
  }, [apiBase, planId]);

  useEffect(() => {
    if (!selectedBundleId) {
      return;
    }
    if (!bundleDatasets.length) {
      setSelectedDatasetId("");
      return;
    }
    const stillValid = bundleDatasets.some((item) => item.id === selectedDatasetId);
    if (!stillValid) {
      setSelectedDatasetId(bundleDatasets[0].id);
    }
  }, [bundleDatasets, selectedBundleId, selectedDatasetId]);

  const savePlan = async (runAfterSave) => {
    setValidationError("");
    setSubmitError("");
    if (!name.trim()) {
      setValidationError("Plan name is required.");
      return;
    }
    if (!selectedBundleId) {
      setValidationError("Select a validated module bundle.");
      return;
    }
    if (!selectedDatasetId) {
      setValidationError("Select a dataset for this plan.");
      return;
    }
    if (!selectedLmProfileId) {
      setValidationError("Select an LM profile.");
      return;
    }

    setIsSaving(true);
    try {
      let runPlanId = "";
      const payload = {
        project_id: PROJECT_ID,
        scenario_id: SCENARIO_ID,
        dataset_version: DATASET_VERSION,
        name: name.trim(),
        runs_per_question: runs,
        max_workers: workers,
        module_import_id: selectedBundleId,
        dataset_id: selectedDatasetId,
        lm_profile_id: selectedLmProfileId || null,
      };

      const savedPlanResp = await fetch(isEditing ? `${apiBase}/evaluation-plans/${planId}` : `${apiBase}/evaluation-plans`, {
        method: isEditing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!savedPlanResp.ok) {
        throw new Error(await parseApiError(savedPlanResp, `Could not save plan (${savedPlanResp.status})`));
      }
      const savedPlan = await savedPlanResp.json();

      if (runAfterSave) {
        const createRunResp = await fetch(`${apiBase}/agent-run-plans`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: PROJECT_ID,
            module_import_id: selectedBundleId,
            scenario_id: SCENARIO_ID,
            dataset_version: DATASET_VERSION,
            bundle_path: selectedBundle?.source_ref || selectedBundle?.checkout_path || selectedBundle?.github_repo_url || selectedBundle?.bundle_name || selectedBundleId,
            eval_inputs: [],
            evaluation_plan_id: savedPlan.id,
            runs_per_question: runs,
            max_workers: workers,
            lm_profile_id: selectedLmProfileId || null,
          }),
        });
        if (!createRunResp.ok) {
          throw new Error(`Plan saved, but run setup failed (${createRunResp.status})`);
        }
        const runPlan = await createRunResp.json();
        runPlanId = runPlan.id;
        const enqueueResp = await fetch(`${apiBase}/agent-run-plans/${runPlan.id}/enqueue`, { method: "POST" });
        if (!enqueueResp.ok) {
          throw new Error(`Plan saved, but run enqueue failed (${enqueueResp.status})`);
        }
      }

      onBack(runAfterSave ? { runPlanId } : { saved: true });
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Could not save plan");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="page">
      <header className="page-head row between plans-builder-head">
        <div className="col gap-1">
          <h1 className="t-h1">{isEditing ? "Edit plan" : "New evaluation plan"}</h1>
          <p className="cap">Choose a bundle, dataset, and LM profile, then tune the execution settings.</p>
        </div>
        <div className="row gap-2">
          <Button onClick={onBack}>Cancel</Button>
          <Button onClick={() => savePlan(false)} disabled={isSaving}>Save</Button>
          <Button variant="primary" onClick={() => savePlan(true)} disabled={isSaving}>Save & run</Button>
        </div>
      </header>

      <div className="plans-builder-body">
        <div className="page-body plans-builder-main">
          {loadError ? <ErrorState title="Could not load plan builder data" description={loadError} /> : null}
          {isLoadingModules || isLoadingDatasets ? <LoadingState label="Loading plan builder data..." /> : null}
          {isLoadingPlan ? <LoadingState label="Loading existing plan..." /> : null}
          {validationError ? <ErrorState title="Validation required" description={validationError} /> : null}
          {submitError ? <ErrorState title="Could not save plan" description={submitError} /> : null}

          <section className="panel card-pad plans-form-block">
            <label className="t-label plans-input-label" htmlFor="plan-name">Plan name</label>
            <input id="plan-name" className="bundles-input" placeholder="e.g. Support triage regression deck" value={name} onChange={(event) => setName(event.target.value)} />
          </section>

          <section className="panel card-pad plans-form-block">
            <div className="row between" style={{ marginBottom: 10 }}>
              <h2 className="t-h2">Module bundle under test</h2>
              <span className="t-label">Validated only</span>
            </div>
            {!validModules.length ? (
              <EmptyState title="No validated bundles" description="Validate a module bundle first, then return to create and run a plan." />
            ) : (
              <div className="col gap-2">
                {validModules.map((bundle) => (
                  <button key={bundle.id} className={`plans-bundle-option ${selectedBundleId === bundle.id ? "plans-bundle-option-active" : ""}`} type="button" onClick={() => setSelectedBundleId(bundle.id)}>
                    <span className="t-sm" style={{ fontWeight: 600 }}>{getBundleDisplayName(bundle)}</span>
                    <span className="cap mono">
                      {bundle.bundle_version ? `v${bundle.bundle_version}` : "version n/a"}
                      {bundle.created_at ? ` · created ${formatCreatedAt(bundle.created_at)}` : ""}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="panel card-pad plans-form-block">
            <div className="row between" style={{ marginBottom: 10 }}>
              <h2 className="t-h2">Dataset</h2>
              <Link className="lnk cap" to={selectedBundleId ? `/datasets/new` : "/datasets"}>{selectedBundleId ? "Create dataset" : "Open datasets"}</Link>
            </div>
            {!selectedBundleId ? (
              <EmptyState title="Select a bundle first" description="Choose a module bundle to see compatible datasets." />
            ) : bundleDatasets.length ? (
              <div className="col gap-2">
                {bundleDatasets.map((dataset) => (
                  <button key={dataset.id} className={`plans-bundle-option ${selectedDatasetId === dataset.id ? "plans-bundle-option-active" : ""}`} type="button" onClick={() => setSelectedDatasetId(dataset.id)}>
                    <span className="t-sm" style={{ fontWeight: 600 }}>{dataset.name || "Untitled dataset"}</span>
                    <span className="cap mono">{dataset.record_count || 0} items · {(dataset.input_keys || []).slice(0, 3).join(", ") || "input"} {"->"} {(dataset.label_keys || []).slice(0, 3).join(", ") || "expected"}</span>
                  </button>
                ))}
              </div>
            ) : (
              <EmptyState title="No datasets for this bundle" description="Create a dataset scoped to this bundle, then come back to attach it to the plan." />
            )}
          </section>

          <section className="panel card-pad plans-form-block">
            <div className="row between" style={{ marginBottom: 10 }}>
              <h2 className="t-h2">LM profile</h2>
              <Link className="lnk cap" to="/lm-profiles">Manage profiles</Link>
            </div>
            <label className="col gap-1" htmlFor="lm-profile-select">
              <span className="t-label">Runtime model profile</span>
              <select id="lm-profile-select" className="bundles-input" value={selectedLmProfileId} onChange={(event) => setSelectedLmProfileId(event.target.value)}>
                <option value="">Select an LM profile...</option>
                {lmProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
                ))}
              </select>
            </label>
          </section>
        </div>

        <aside className="plans-rail">
          <div className="col gap-3">
            <div className="t-label">Agent Run Plan</div>
            <StepControl label="Runs per input" value={runs} min={1} setValue={setRuns} />
            <StepControl label="Max workers" value={workers} min={1} max={24} setValue={setWorkers} />
            <hr className="hr" />
            <div className="panel card-pad col gap-2">
              <div className="row between"><span className="muted">Bundle</span><span className="mono">{selectedBundle ? getBundleDisplayName(selectedBundle) : "none"}</span></div>
              <div className="row between"><span className="muted">Dataset</span><span className="mono">{selectedDataset?.record_count || 0} items</span></div>
              <div className="row between"><span className="muted">x Runs per input</span><span className="mono">{runs}</span></div>
              <hr className="hr" />
              <div className="row between"><span>Total tasks</span><span className="mono">{totalTasks}</span></div>
            </div>
            <div className="plans-note row gap-2">
              <span className="cap">Datasets are now authored separately and reused across plans. Choose one compatible with the selected bundle.</span>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}

function StepControl({ label, value, setValue, min, max }) {
  return (
    <div className="col gap-2">
      <div className="field-label">{label}</div>
      <div className="row gap-2">
        <Button size="sm" onClick={() => setValue(Math.max(min, value - 1))} aria-label={`Decrease ${label}`}>
          <span style={{ fontSize: 16, marginTop: -1 }}>-</span>
        </Button>
        <div className="plans-step-value mono">{value}</div>
        <Button
          size="sm"
          onClick={() => setValue(typeof max === "number" ? Math.min(max, value + 1) : value + 1)}
          aria-label={`Increase ${label}`}
        >
          <span style={{ fontSize: 16, marginTop: -1 }}>+</span>
        </Button>
      </div>
    </div>
  );
}
