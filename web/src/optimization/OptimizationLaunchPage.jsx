import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { Icon } from "../components/Icon";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const PROJECT_ID = "proj-1";

const STRATEGY_OPTIONS = [
  {
    id: "bootstrap_fewshot",
    label: "BootstrapFewShot",
    objective: "optimize_demo_quality",
    datasetKind: "demo",
    sourceType: "eval_passes",
    requestConfigDefaults: {
      max_bootstrapped_demos: 4,
      max_labeled_demos: 16,
    },
    description: "Bootstrap-style optimization from few-shot demos with a strong execution + fallback teacher model path.",
    sourcePreviewHint: "Demo dataset is derived from eval examples that pass the score threshold.",
  },
  {
    id: "miprov2",
    label: "MIPROv2",
    objective: "optimize_demo_quality",
    datasetKind: "demo",
    sourceType: "eval_passes",
    requestConfigDefaults: {
      budget: "light",
      max_bootstrapped_demos: 4,
      max_labeled_demos: 16,
    },
    description: "Prompt synthesis and selection with separate execution and helper roles for richer candidate search.",
    sourcePreviewHint: "Demo dataset is derived from eval examples that pass the configured score threshold.",
  },
  {
    id: "gepa",
    label: "GEPA",
    objective: "optimize_judge_feedback",
    datasetKind: "feedback",
    sourceType: "eval_feedback",
    requestConfigDefaults: {
      budget: "light",
      track_stats: true,
    },
    description: "Generation-anchored optimization that tunes from feedback-style datasets using a judge loop.",
    sourcePreviewHint: "Feedback dataset is derived from eval outputs and includes both pass/fail signal for judge feedback.",
  },
];

const STRATEGY_BY_ID = Object.fromEntries(STRATEGY_OPTIONS.map((strategy) => [strategy.id, strategy]));

function toArray(payload) {
  return Array.isArray(payload) ? payload : [];
}

function toItemList(payload) {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (payload && Array.isArray(payload.items)) {
    return payload.items;
  }
  return [];
}

function formatDateTime(value) {
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

function resolveBundlePath(moduleRow) {
  return moduleRow?.source_ref || moduleRow?.bundle_name || "uploaded-bundle.zip";
}

function formatRunPlanLabel(plan) {
  if (!plan) {
    return "";
  }
  const name = plan.plan_name || plan.id || "Run plan";
  return `${name} (${plan.status || "unknown"}, ${formatDateTime(plan.created_at)})`;
}

function summarizeRunPlanTasks(tasks, sourceType) {
  const taskRows = Array.isArray(tasks) ? tasks : [];
  const passCount = taskRows.filter((task) => task?.eval_pass === true).length;
  const failCount = taskRows.filter((task) => task?.eval_pass === false).length;
  if (sourceType === "eval_feedback") {
    return {
      record_count: taskRows.length,
      provenance_summary: {
        included_records: taskRows.length,
        excluded_records: 0,
        pass_count: passCount,
        fail_count: failCount,
        excluded_reasons: {},
      },
      preview: true,
    };
  }
  return {
    record_count: passCount,
    provenance_summary: {
      included_records: passCount,
      excluded_records: failCount,
      excluded_reasons: failCount > 0 ? { score_below_threshold: failCount } : {},
    },
    preview: true,
  };
}

export function OptimizationLaunchPage() {
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
  const [modules, setModules] = useState([]);
  const [lmProfiles, setLmProfiles] = useState([]);
  const [selectedModuleId, setSelectedModuleId] = useState("");
  const [selectedStrategy, setSelectedStrategy] = useState(STRATEGY_OPTIONS[0]?.id || "bootstrap_fewshot");
  const [executionLmProfileId, setExecutionLmProfileId] = useState("");
  const [helperLmProfileId, setHelperLmProfileId] = useState("");
  const [sourceRunPlanId, setSourceRunPlanId] = useState("");
  const [sourceRunPlans, setSourceRunPlans] = useState([]);
  const [isLoadingSourceRunPlans, setIsLoadingSourceRunPlans] = useState(false);
  const [sourceDatasetPreview, setSourceDatasetPreview] = useState(null);
  const [isLoadingSourcePreview, setIsLoadingSourcePreview] = useState(false);
  const [sourcePreviewError, setSourcePreviewError] = useState("");
  const [isLoadingModules, setIsLoadingModules] = useState(false);
  const [isLoadingProfiles, setIsLoadingProfiles] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [validationError, setValidationError] = useState("");
  const [createdJobId, setCreatedJobId] = useState("");

  const validModules = useMemo(() => modules.filter((item) => item.validation_status === "passed"), [modules]);
  const selectedModule = useMemo(() => modules.find((item) => item.id === selectedModuleId) || null, [modules, selectedModuleId]);
  const selectedStrategyDef = STRATEGY_BY_ID[selectedStrategy] || STRATEGY_OPTIONS[0];
  const isLoading = isLoadingModules || isLoadingProfiles;

  const defaultRequestConfig = useMemo(
    () => ({
      ...(selectedStrategyDef?.requestConfigDefaults ? { ...selectedStrategyDef.requestConfigDefaults } : {}),
    }),
    [selectedStrategyDef],
  );

  useEffect(() => {
    const loadModules = async () => {
      setIsLoadingModules(true);
      setError("");
      try {
        const modulesResp = await fetch(`${apiBase}/modules`, { method: "GET" });
        if (!modulesResp.ok) {
          throw new Error(`Could not load modules (${modulesResp.status})`);
        }
        const payload = await modulesResp.json();
        const moduleRows = toArray(payload);
        setModules(moduleRows);
        if (!selectedModuleId && moduleRows.length) {
          const firstValid = moduleRows.find((item) => item.validation_status === "passed");
          if (firstValid) {
            setSelectedModuleId(firstValid.id);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load modules");
      } finally {
        setIsLoadingModules(false);
      }
    };

    const loadProfiles = async () => {
      setIsLoadingProfiles(true);
      try {
        const response = await fetch(`${apiBase}/lm-profiles`, { method: "GET" });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        const profileRows = toArray(payload);
        setLmProfiles(profileRows);
        if (!executionLmProfileId && profileRows.length) {
          setExecutionLmProfileId(profileRows[0].id || "");
        }
      } catch {
        setLmProfiles([]);
      } finally {
        setIsLoadingProfiles(false);
      }
    };

    loadModules();
    loadProfiles();
  }, [apiBase, executionLmProfileId, selectedModuleId]);

  useEffect(() => {
    const loadSourceRunPlans = async () => {
      if (!selectedModuleId) {
        setSourceRunPlans([]);
        setSourceRunPlanId("");
        setSourceDatasetPreview(null);
        setSourcePreviewError("");
        return;
      }

      setIsLoadingSourceRunPlans(true);
      try {
        const response = await fetch(`${apiBase}/modules/${selectedModuleId}/agent-run-plans?limit=100&offset=0`, { method: "GET" });
        if (!response.ok) {
          setSourceRunPlans([]);
          setSourceRunPlanId("");
          setSourcePreviewError("");
          return;
        }
        const payload = await response.json();
        const runPlans = toItemList(payload);
        setSourceRunPlans(runPlans);
        setSourceRunPlanId((currentId) => (runPlans.some((runPlan) => runPlan?.id === currentId) ? currentId : ""));
        setSourcePreviewError("");
      } catch {
        setSourceRunPlans([]);
        setSourceRunPlanId("");
        setSourcePreviewError("");
      } finally {
        setIsLoadingSourceRunPlans(false);
      }
    };

    loadSourceRunPlans();
  }, [apiBase, selectedModuleId]);

  useEffect(() => {
    const loadSourcePreview = async () => {
      if (!selectedModuleId || !sourceRunPlanId || !selectedStrategyDef?.datasetKind || !selectedStrategyDef?.sourceType) {
        setSourceDatasetPreview(null);
        setSourcePreviewError("");
        setIsLoadingSourcePreview(false);
        return;
      }

      setIsLoadingSourcePreview(true);
      setSourcePreviewError("");
      try {
        const response = await fetch(`${apiBase}/agent-run-plans/${sourceRunPlanId}/tasks?limit=500&offset=0`, { method: "GET" });
        if (!response.ok) {
          setSourcePreviewError("Could not load source run plan tasks.");
          setSourceDatasetPreview(null);
          return;
        }
        const payload = await response.json();
        setSourceDatasetPreview(summarizeRunPlanTasks(toItemList(payload), selectedStrategyDef.sourceType));
      } catch {
        setSourcePreviewError("Could not load source run plan tasks.");
        setSourceDatasetPreview(null);
      } finally {
        setIsLoadingSourcePreview(false);
      }
    };

    loadSourcePreview();
  }, [apiBase, selectedModuleId, sourceRunPlanId, selectedStrategyDef]);

  const submit = async () => {
    setValidationError("");
    setError("");
    setCreatedJobId("");
    if (!selectedModuleId) {
      setValidationError("Select a validated module bundle.");
      return;
    }
    if (!executionLmProfileId) {
      setValidationError("Execution LM profile is required to launch optimization.");
      return;
    }
    if (!sourceRunPlanId.trim()) {
      setValidationError("Source run plan is required to launch optimization.");
      return;
    }
    setIsSubmitting(true);
    try {
      const response = await fetch(`${apiBase}/optimization/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: PROJECT_ID,
          module_import_id: selectedModuleId,
          bundle_path: resolveBundlePath(selectedModule),
          strategy: selectedStrategy,
          objective: selectedStrategyDef.objective || "optimize_demo_quality",
          dataset_id: null,
          validation_dataset_id: null,
          execution_lm_profile_id: executionLmProfileId,
          helper_lm_profile_id: helperLmProfileId || null,
          request_config: defaultRequestConfig,
          normalized_config: {
            optimizer_family: selectedStrategy,
          },
          train_inputs: [],
          val_inputs: [],
            num_threads: 1,
            source_run_plan_id: sourceRunPlanId.trim(),
        }),
      });
      if (!response.ok) {
        const detail = await readApiError(response);
        throw new Error(detail || `Could not launch optimization job (${response.status})`);
      }
      const payload = await response.json();
      setCreatedJobId(typeof payload?.id === "string" ? payload.id : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not launch optimization job");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="page">
      <div className="page-body optimization-wrap">
        <header className="row between optimization-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Optimization Launch</h1>
            <p className="muted t-sm">Create an optimization run with strategy family, role routing, and module context.</p>
          </div>
          <Button variant="primary" onClick={submit} disabled={isSubmitting || isLoading}>
            {isSubmitting ? "Launching..." : "Launch optimization job"}
          </Button>
        </header>

        {isLoading ? <LoadingState label="Loading optimization context..." /> : null}
        {validationError ? <ErrorState title="Validation required" description={validationError} /> : null}
        {error ? <ErrorState title="Could not launch optimization" description={error} /> : null}

        {createdJobId ? (
          <div className="optimization-success" role="status" aria-live="polite">
            <div className="row gap-2" style={{ marginBottom: 8 }}>
              <Icon name="activity" size={14} />
              <span className="optimization-success-title">Optimization job queued</span>
            </div>
            <span className="optimization-success-id">Job ID: {createdJobId}</span>
            <div className="optimization-success-copy">
              Open <Link className="lnk" to={`/optimization/jobs?job=${encodeURIComponent(createdJobId)}`}>optimization job monitoring</Link>.
            </div>
          </div>
        ) : null}

        <section className="panel card-pad optimization-form-block">
          <h2 className="t-h2">Target module and context</h2>

          <label className="col gap-1" htmlFor="optimization-module-select">
            <span className="t-label">Target module</span>
            <select
              id="optimization-module-select"
              className="bundles-input"
              value={selectedModuleId}
              onChange={(event) => setSelectedModuleId(event.target.value)}
            >
              <option value="">Select a validated module...</option>
                {validModules.map((item) => (
                  <option key={item.id} value={item.id}>
                    {`${item.bundle_name || item.source_ref || item.id} (v${item.bundle_version || "unknown"}, ${formatDateTime(item.created_at)})`}
                  </option>
                ))}
              </select>
            </label>

          <FieldHelp text="The selected module is the source for optimization. Re-run its bundle upload if this is stale." />

          <label className="col gap-1" htmlFor="optimization-source-run-plan">
            <span className="t-label">Source run plan</span>
            <select
              id="optimization-source-run-plan"
              className="bundles-input"
              value={sourceRunPlanId}
              onChange={(event) => setSourceRunPlanId(event.target.value)}
              disabled={!selectedModuleId}
            >
              <option value="">
                {selectedModuleId
                  ? isLoadingSourceRunPlans
                    ? "Loading source run plans..."
                    : sourceRunPlans.length
                      ? "Select a run plan..."
                      : "No matching run plans for this module"
                  : "Select a validated module first"}
              </option>
              {sourceRunPlans.map((runPlan) => (
                <option key={runPlan.id} value={runPlan.id}>
                  {formatRunPlanLabel(runPlan)}
                </option>
              ))}
            </select>
          </label>
          <FieldHelp
            text={
              !selectedModuleId
                ? "Select a validated module to scope source run plans by module."
                : sourceRunPlans.length
                  ? "Required. Choose the source run plan used to derive optimization data and follow-up evaluation context."
                  : "No source run plans were found for the selected module."
            }
          />

          <div className="col gap-2" style={{ marginTop: 8 }}>
            <h3 className="t-label">Source dataset preview</h3>
            <div className="optimization-source-preview">
              {!sourceRunPlanId ? <span className="muted">Select a source run plan to preview derived dataset stats.</span> : null}
              {sourceRunPlanId && isLoadingSourcePreview ? <LoadingState label="Deriving source dataset preview..." /> : null}
              {sourceRunPlanId && sourcePreviewError ? <ErrorState title="Could not load source preview" description={sourcePreviewError} /> : null}
              {sourceRunPlanId && sourceDatasetPreview ? (
                <div className="col gap-1">
                  {selectedStrategyDef.sourceType === "eval_feedback" ? (
                    <>
                      <p>
                        <strong>Total runs:</strong> {sourceDatasetPreview?.record_count || 0}
                      </p>
                      <p>
                        <strong>Passing runs:</strong> {sourceDatasetPreview?.provenance_summary?.pass_count || 0}
                        <span className="muted"> / Failing runs: {sourceDatasetPreview?.provenance_summary?.fail_count || 0}</span>
                      </p>
                    </>
                  ) : (
                    <>
                      <p>
                        <strong>Expected records:</strong> {sourceDatasetPreview?.record_count || 0}
                      </p>
                      <p>
                        <strong>Included:</strong> {sourceDatasetPreview?.provenance_summary?.included_records || 0}
                        <span className="muted"> / Excluded: {sourceDatasetPreview?.provenance_summary?.excluded_records || 0}</span>
                      </p>
                    </>
                  )}
                  {Object.keys(sourceDatasetPreview?.provenance_summary?.excluded_reasons || {}).length ? (
                    <ul className="optimization-preview-excluded-list">
                      {Object.entries(sourceDatasetPreview.provenance_summary.excluded_reasons).map(([reason, count]) => (
                        <li key={`${reason}-${count}`}>{`${reason}: ${count}`}</li>
                      ))}
                    </ul>
                  ) : null}
                  <p className="optimization-source-preview-note">{selectedStrategyDef.sourcePreviewHint}</p>
                </div>
              ) : null}
            </div>
          </div>
        </section>

        <section className="panel card-pad optimization-form-block">
          <div className="row between" style={{ marginBottom: 8 }}>
            <h2 className="t-h2">Strategy family</h2>
            <span className="t-label">Core defaults applied</span>
          </div>
          <div className="optimization-strategy-grid">
            {STRATEGY_OPTIONS.map((strategy) => (
                <button
                  key={strategy.id}
                  type="button"
                  aria-label={strategy.label}
                  className={`optimization-strategy-card ${selectedStrategy === strategy.id ? "optimization-strategy-card-active" : ""}`}
                  onClick={() => setSelectedStrategy(strategy.id)}
                >
                <div className="row between">
                  <span className="t-h2">{strategy.label}</span>
                  {selectedStrategy === strategy.id ? <span className="optimization-strategy-badge">Selected</span> : null}
                </div>
                <div className="cap muted" style={{ marginTop: 6 }}>{strategy.description}</div>
                <pre className="optimization-request-config-preview" aria-label={`${strategy.label} defaults`}>
                  {JSON.stringify(strategy.requestConfigDefaults, null, 2)}
                </pre>
              </button>
            ))}
          </div>
          <FieldHelp
            text={`${selectedStrategyDef.label} objective: ${selectedStrategyDef.objective}. This scaffold currently submits offline execution with defaults and selected roles. ${selectedStrategyDef.sourcePreviewHint || ""}`}
          />
        </section>

        <section className="panel card-pad optimization-form-block optimization-grid-2">
          <div className="col gap-2">
            <h2 className="t-h2">Execution roles</h2>
            <label className="col gap-1" htmlFor="optimization-exec-lm">
              <span className="t-label">Execution LM profile</span>
              <select
                id="optimization-exec-lm"
                className="bundles-input"
                value={executionLmProfileId}
                onChange={(event) => setExecutionLmProfileId(event.target.value)}
              >
                <option value="">Select an execution LM...</option>
                {lmProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
                ))}
              </select>
            </label>
            <FieldHelp text="Execution model does optimization work for each candidate and is always required." />
          </div>

          <div className="col gap-2">
            <h2 className="t-h2">Helper role</h2>
            <label className="col gap-1" htmlFor="optimization-helper-lm">
              <span className="t-label">Helper LM profile (optional)</span>
              <select
                id="optimization-helper-lm"
                className="bundles-input"
                value={helperLmProfileId}
                onChange={(event) => setHelperLmProfileId(event.target.value)}
              >
                <option value="">Use execution LM as fallback</option>
                {lmProfiles.map((profile) => (
                  <option key={`helper-${profile.id}`} value={profile.id}>{profile.name || profile.id}</option>
                ))}
              </select>
            </label>
            <FieldHelp
              text={
                selectedStrategy === "bootstrap_fewshot"
                  ? "Acts as teacher_lm_profile_id when provided for BootstrapFewShot."
                  : selectedStrategy === "miprov2"
                  ? "Acts as prompt_model_lm_profile_id for MIPROv2 prompt proposals."
                  : "Acts as reflection_lm_profile_id for GEPA and falls back to execution profile when empty."
              }
            />
          </div>
        </section>
      </div>
    </section>
  );
}

function FieldHelp({ text }) {
  return (
    <div className="field-help field-help--optimization row gap-2" role="note">
      <Icon name="info" size={13} className="field-help-icon" />
      <span className="cap field-help-copy">{text}</span>
    </div>
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
