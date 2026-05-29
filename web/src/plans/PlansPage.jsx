import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const PROJECT_ID = "proj-1";
const SCENARIO_ID = "scn-1";
const DATASET_VERSION = "v1";

const SAMPLE_ROWS = [
  {
    input: "A customer says: 'I was charged twice for order #8842.'",
    expected: "Acknowledge duplicate charge concern and ask for transaction details.",
  },
  {
    input: "Classify this request as urgent or non-urgent: 'My account is locked and payroll runs in 30 min.'",
    expected: "urgent",
  },
];

export function PlansPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const showBuilder = searchParams.get("new") === "1";
  const editingPlanId = searchParams.get("id") || "";
  const savedFlag = searchParams.get("saved") === "1";

  return showBuilder ? <PlanBuilder onBack={(opts) => {
    if (opts?.runPlanId) {
      navigate(`/runs?plan=${encodeURIComponent(opts.runPlanId)}`);
      return;
    }
    navigate(opts?.saved ? "/plans?saved=1" : "/plans");
  }} planId={editingPlanId} /> : <PlansList onCreate={() => navigate("/plans?new=1")} onEdit={(id) => navigate(`/plans?new=1&id=${encodeURIComponent(id)}`)} showSavedNotice={savedFlag} />;
}

function PlansList({ onCreate, onEdit, showSavedNotice }) {
  const plansUrl = useMemo(() => `${(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}/evaluation-plans`, []);
  const [plans, setPlans] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingPlanId, setDeletingPlanId] = useState("");

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
            <p className="muted t-sm">Question sets + expected answers for reusable agent checks.</p>
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
              {plans.map((plan) => {
                const count = Array.isArray(plan.eval_inputs) ? plan.eval_inputs.length : 0;
                return (
                  <article key={plan.id} className="panel card-pad plans-row">
                    <div className="plans-row-icon center">
                      <Icon name="layers" size={18} />
                    </div>
                    <div className="col gap-1" style={{ flex: 1 }}>
                      <div className="row gap-2">
                        <button type="button" className="plans-name-link" onClick={() => onEdit(plan.id)}>{plan.name || "Untitled plan"}</button>
                      </div>
                      <span className="cap mono">{count} questions x {plan.runs_per_question || 1} runs = {count * (plan.runs_per_question || 1)} tasks · {plan.max_workers || 1} workers</span>
                    </div>
                    <div className="row gap-2">
                      <Button size="sm" variant="primary" icon="activity" onClick={(event) => event.stopPropagation()}>
                        Run
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
                );
              })}
            </div>
          ) : (
            <EmptyState title="No plans yet" description="Create your first evaluation plan from a validated module bundle." />
          )
        ) : null}
      </div>
    </section>
  );
}

function PlanBuilder({ onBack, planId }) {
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [modules, setModules] = useState([]);
  const [isLoadingModules, setIsLoadingModules] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [validationError, setValidationError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [name, setName] = useState("");
  const [runs, setRuns] = useState(3);
  const [workers, setWorkers] = useState(8);
  const [rows, setRows] = useState([{ id: "q-1", input: "", expected: "" }]);
  const [selectedBundleId, setSelectedBundleId] = useState("");
  const [isLoadingPlan, setIsLoadingPlan] = useState(false);
  const isEditing = Boolean(planId);

  const validModules = useMemo(() => modules.filter((item) => item.validation_status === "passed"), [modules]);
  const filledRows = useMemo(() => rows.filter((item) => item.input.trim() && item.expected.trim()), [rows]);
  const totalTasks = filledRows.length * runs;

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

  useEffect(() => {
    loadModules();
  }, []);

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
        if (typeof payload.module_import_id === "string" && payload.module_import_id) {
          setSelectedBundleId(payload.module_import_id);
        }
        const fromInputs = Array.isArray(payload.eval_inputs)
          ? payload.eval_inputs.map((item, idx) => ({
              id: `loaded-${idx}`,
              input: item?.input?.question || "",
              expected: item?.label?.expected || "",
            }))
          : [];
        if (fromInputs.length) {
          setRows(fromInputs);
        }
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : "Could not load plan");
      } finally {
        setIsLoadingPlan(false);
      }
    };
    loadPlan();
  }, [apiBase, planId]);

  const updateRow = (id, field, value) => {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  };

  const addRow = () => {
    setRows((prev) => [...prev, { id: `q-${Date.now()}`, input: "", expected: "" }]);
  };

  const deleteRow = (id) => {
    setRows((prev) => (prev.length > 1 ? prev.filter((row) => row.id !== id) : prev));
  };

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
    if (!filledRows.length) {
      setValidationError("Add at least one question and expected answer.");
      return;
    }

    setIsSaving(true);
    try {
      let runPlanId = "";

      const evalInputs = filledRows.map((row) => ({ input: { question: row.input }, label: { expected: row.expected } }));

      const createdPlanResp = await fetch(`${apiBase}/evaluation-plans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: PROJECT_ID,
          scenario_id: SCENARIO_ID,
          dataset_version: DATASET_VERSION,
          eval_inputs: evalInputs,
          name: name.trim(),
          runs_per_question: runs,
          max_workers: workers,
          module_import_id: selectedBundleId,
        }),
      });
      if (!createdPlanResp.ok) {
        throw new Error(`Could not save plan (${createdPlanResp.status})`);
      }
      const createdPlan = await createdPlanResp.json();

      if (runAfterSave) {
        const selectedBundle = validModules.find((item) => item.id === selectedBundleId);
        const createRunResp = await fetch(`${apiBase}/agent-run-plans`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_id: PROJECT_ID,
            module_import_id: selectedBundleId,
            scenario_id: SCENARIO_ID,
            dataset_version: DATASET_VERSION,
            bundle_path: selectedBundle?.source_ref || selectedBundle?.bundle_name || "uploaded-bundle.zip",
            eval_inputs: [],
            evaluation_plan_id: createdPlan.id,
            runs_per_question: runs,
            max_workers: workers,
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
          <p className="cap">Define questions and expected answers, then tune stress configuration.</p>
        </div>
        <div className="row gap-2">
          <Button onClick={onBack}>Cancel</Button>
          <Button onClick={() => savePlan(false)} disabled={isSaving}>Save</Button>
          <Button variant="primary" onClick={() => savePlan(true)} disabled={isSaving}>Save & run</Button>
        </div>
      </header>

      <div className="plans-builder-body">
        <div className="page-body plans-builder-main">
          {loadError ? <ErrorState title="Could not load bundles" description={loadError} /> : null}
          {isLoadingModules ? <LoadingState label="Loading module bundles..." /> : null}
          {isLoadingPlan ? <LoadingState label="Loading existing plan..." /> : null}
          {validationError ? (
            <div className="plans-validation-alert" role="alert" aria-live="polite">
              <div className="row gap-2">
                <Icon name="activity" size={14} />
                <span className="plans-validation-title">Validation required</span>
              </div>
              <p className="plans-validation-copy">{validationError}</p>
            </div>
          ) : null}
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
                    <span className="t-sm" style={{ fontWeight: 600 }}>{bundle.bundle_name || bundle.source_ref || bundle.id}</span>
                    <span className="cap mono">
                      {bundle.bundle_version ? `v${bundle.bundle_version}` : "version n/a"} · {bundle.source_ref || "uploaded bundle"}
                      {bundle.created_at ? ` · created ${formatCreatedAt(bundle.created_at)}` : ""}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="panel card-pad plans-form-block">
            <div className="row between" style={{ marginBottom: 10 }}>
              <div className="row gap-2"><h2 className="t-h2">Questions</h2><span className="plans-count">{filledRows.length}</span></div>
              <div className="row gap-2">
                <Button size="sm" onClick={() => setRows(SAMPLE_ROWS.map((row, idx) => ({ id: `sample-${idx}`, ...row })))}>Load sample set</Button>
                <Button size="sm" onClick={addRow}>Add question</Button>
              </div>
            </div>
            <div className="col gap-2">
              <div className="plans-row-head">
                <span className="t-label">#</span>
                <span className="t-label">Input prompt</span>
                <span className="t-label">Expected answer</span>
                <span />
              </div>
              {rows.map((row, index) => (
                <div key={row.id} className="plans-row-edit">
                  <span className="cap mono">{index + 1}</span>
                  <textarea className="plans-textarea" rows={2} value={row.input} onChange={(event) => updateRow(row.id, "input", event.target.value)} placeholder="Input prompt" />
                  <textarea className="plans-textarea" rows={2} value={row.expected} onChange={(event) => updateRow(row.id, "expected", event.target.value)} placeholder="Expected answer" />
                  <Button size="sm" variant="danger" onClick={() => deleteRow(row.id)}>Delete</Button>
                </div>
              ))}
            </div>
          </section>
        </div>

        <aside className="plans-rail">
          <div className="col gap-3">
            <div className="t-label">Agent Run Plan</div>
            <StepControl label="Runs per question" value={runs} min={1} max={20} setValue={setRuns} />
            <StepControl label="Max workers" value={workers} min={1} max={24} setValue={setWorkers} />
            <hr className="hr" />
            <div className="panel card-pad col gap-2">
              <div className="row between"><span className="muted">Questions</span><span className="mono">{filledRows.length}</span></div>
              <div className="row between"><span className="muted">x Runs per question</span><span className="mono">{runs}</span></div>
              <hr className="hr" />
              <div className="row between"><span>Total tasks</span><span className="mono">{totalTasks}</span></div>
            </div>
            <div className="plans-note row gap-2">
              <Icon name="search" size={14} />
              <span className="cap">Each task opens a run item with score, rationale, and trace context.</span>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
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

function StepControl({ label, value, setValue, min, max }) {
  return (
    <div className="col gap-2">
      <div className="field-label">{label}</div>
      <div className="row gap-2">
        <Button size="sm" onClick={() => setValue(Math.max(min, value - 1))} aria-label={`Decrease ${label}`}>
          <span style={{ fontSize: 16, marginTop: -1 }}>-</span>
        </Button>
        <div className="plans-step-value mono">{value}</div>
        <Button size="sm" onClick={() => setValue(Math.min(max, value + 1))} aria-label={`Increase ${label}`}>
          <span style={{ fontSize: 16, marginTop: -1 }}>+</span>
        </Button>
      </div>
    </div>
  );
}
