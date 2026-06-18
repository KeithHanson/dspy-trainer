import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const VALIDATION_CHECKS = [
  {
    id: "module_file",
    label: "module.py exists",
    failCodes: ["module_missing"],
    help: "Add a module.py file at the bundle root. It should include your DSPy module implementation and build_program().",
    snippet: `import dspy

class TicketSignature(dspy.Signature):
    ticket = dspy.InputField()
    history = dspy.InputField()
    category = dspy.OutputField()
    priority = dspy.OutputField()
    reply = dspy.OutputField()

class TriageAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.respond = dspy.ChainOfThought(TicketSignature)

    def forward(self, ticket: str, history: str):
        return self.respond(ticket=ticket, history=history)

def build_program():
    return TriageAgent()
`,
  },
  {
    id: "metric_file",
    label: "metric.py exists",
    failCodes: ["metric_missing"],
    help: "Add a metric.py file at the bundle root with judge_metric(example, prediction, trace=None). judge_metric must return a dict with score/rationale/flags/raw_response.",
    snippet: `def judge_metric(example, prediction, trace=None):
    expected_answer = str(example.label.get("expected", "")).strip()

    category = str(getattr(prediction, "category", "")).strip().lower()
    priority = str(getattr(prediction, "priority", "")).strip().lower()
    reply = str(getattr(prediction, "reply", "")).strip()

    score = 1.0 if reply else 0.0
    return {
        "score": score,
        "rationale": "has_reply" if reply else "missing_reply",
        "flags": [] if reply else ["missing_reply"],
        "raw_response": {
            "expected_answer": expected_answer,
            "category": category,
            "priority": priority,
        },
    }
`,
  },
  {
    id: "bundle_toml_file",
    label: "bundle.toml exists",
    failCodes: ["bundle_toml_missing"],
    help: "Add a bundle.toml file at the bundle root so the validator can read required metadata.",
    snippet: `name = "support-triage-agent"
version = "0.1.0"
score_pass_threshold = 0.8
`,
  },
  {
    id: "signature",
    label: "DSPy Signature declared",
    failCodes: ["signature_missing"],
    help: "Define at least one DSPy Signature class in module.py with InputField and OutputField members.",
    snippet: `class TicketSignature(dspy.Signature):
    ticket = dspy.InputField(desc="Incoming support ticket")
    history = dspy.InputField(desc="Conversation history")
    category = dspy.OutputField(desc="Issue category")
    priority = dspy.OutputField(desc="low | medium | high")
    reply = dspy.OutputField(desc="Customer response")
`,
  },
  {
    id: "module_class",
    label: "dspy.Module subclass declared",
    failCodes: ["module_missing_class"],
    help: "Create a class in module.py that inherits from dspy.Module and implements your forward() behavior.",
    snippet: `class TriageAgent(dspy.Module):
    def __init__(self):
        super().__init__()
        self.respond = dspy.ChainOfThought(TicketSignature)

    def forward(self, ticket: str, history: str):
        return self.respond(ticket=ticket, history=history)
`,
  },
  {
    id: "build_program",
    label: "build_program() exported",
    failCodes: ["build_program_missing"],
    help: "Export a build_program() function in module.py that returns an instance of your dspy.Module subclass.",
    snippet: `def build_program():
    return TriageAgent()
`,
  },
  {
    id: "judge_metric",
    label: "judge_metric(example, prediction) exists",
    failCodes: ["judge_metric_missing", "judge_metric_signature_invalid"],
    help: "Define judge_metric(example, prediction, trace=None) in metric.py and return a dict with keys: score, rationale, flags, raw_response.",
    snippet: `def judge_metric(example, prediction, trace=None):
    expected_answer = str(example.label.get("expected", "")).strip()

    category = str(getattr(prediction, "category", "")).strip().lower()
    priority = str(getattr(prediction, "priority", "")).strip().lower()
    reply = str(getattr(prediction, "reply", "")).strip()

    return {
        "score": 1.0 if reply else 0.0,
        "rationale": "has_reply" if reply else "missing_reply",
        "flags": [] if reply else ["missing_reply"],
        "raw_response": {
            "expected_answer": expected_answer,
            "category": category,
            "priority": priority,
        },
    }
`,
  },
  {
    id: "python_syntax",
    label: "Python syntax is valid",
    failCodes: ["syntax_error", "read_error"],
    help: "Ensure module.py and metric.py are valid Python and UTF-8 readable. Run `python -m py_compile module.py metric.py` locally.",
    snippet: `# run from your bundle directory
python -m py_compile module.py metric.py

# if successful, this command prints nothing and exits 0
`,
  },
  {
    id: "bundle_toml_fields",
    label: "bundle.toml has required fields",
    failCodes: ["bundle_toml_invalid", "bundle_toml_name_missing", "bundle_toml_version_missing", "bundle_toml_score_pass_threshold_invalid"],
    help: "bundle.toml must be valid TOML and include non-empty name/version plus numeric score_pass_threshold between 0.0 and 1.0.",
    snippet: `name = "support-triage-agent"
version = "0.1.0"
score_pass_threshold = 0.8
`,
  },
];

function buildApiUrl(path) {
  const base = import.meta.env.VITE_API_BASE_URL?.trim();
  if (!base) return path;
  return `${base.replace(/\/$/, "")}${path}`;
}

const IMPORT_GUIDANCE = [
  "Repository root is cloned exactly as-is, then an optional subfolder can be validated as the bundle root.",
  "The selected bundle root must contain module.py, metric.py, and bundle.toml.",
  "GitHub access is provided by the backend environment; the browser never collects the token.",
  "Import fails immediately if the selected bundle root does not satisfy the bundle contract.",
];

const BUNDLE_FILE_TEMPLATES = {
  "module.py": VALIDATION_CHECKS.find((check) => check.id === "module_file")?.snippet || "# module.py not available",
  "metric.py": VALIDATION_CHECKS.find((check) => check.id === "metric_file")?.snippet || "# metric.py not available",
  "bundle.toml": VALIDATION_CHECKS.find((check) => check.id === "bundle_toml_file")?.snippet || "# bundle.toml not available",
};

function parseEnvironmentPaste(text) {
  const entries = [];
  const lines = String(text || "").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const match = trimmed.match(/^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/);
    if (!match) {
      continue;
    }
    let [, key, value] = match;
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    entries.push({ key, value, is_secret: false });
  }
  return entries;
}

function mergeEnvironmentEntries(current, additions) {
  const next = [];
  const byKey = new Map();
  current.forEach((entry) => {
    if (!entry?.key) {
      return;
    }
    const normalized = { ...entry };
    byKey.set(entry.key, normalized);
    next.push(normalized);
  });
  additions.forEach((entry) => {
    if (!entry?.key) {
      return;
    }
    const existing = byKey.get(entry.key);
    if (existing) {
      existing.value = entry.value;
      return;
    }
    const normalized = { ...entry };
    byKey.set(entry.key, normalized);
    next.push(normalized);
  });
  return next;
}

export function BundlesPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { moduleId } = useParams();
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const showImportIntent = searchParams.get("import") === "1";

  const sampleUrl = useMemo(() => buildApiUrl("/samples/module-bundle"), []);
  const validateUrl = useMemo(() => buildApiUrl("/modules"), []);

  if (moduleId) {
    return <BundleDetailPage moduleId={moduleId} modulesUrl={validateUrl} onBack={() => navigate("/bundles")} />;
  }

  const handleDownload = async () => {
    setIsDownloading(true);
    setDownloadError("");
    try {
      const response = await fetch(sampleUrl, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Sample download failed (${response.status})`);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "example-bundle.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Could not download sample bundle");
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <section className="page">
      <div className="page-body bundles-wrap">
        <header className="row between bundles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Module Bundles</h1>
            <p className="muted t-sm">GitHub-backed DSPy bundles with <span className="mono">module.py</span>, <span className="mono">metric.py</span>, and <span className="mono">bundle.toml</span> at the repo root or an imported subfolder.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={handleDownload} disabled={isDownloading}>
              {isDownloading ? "Downloading..." : "Download example"}
            </Button>
            <Button variant="primary" onClick={() => navigate("/bundles?import=1")}>Import from GitHub</Button>
          </div>
        </header>

        {downloadError ? <ErrorState title="Download failed" description={downloadError} /> : null}

        {showImportIntent ? (
          <>
            <section className="panel card-pad bundles-section">
              <div className="row between">
                <h2 className="t-h2">Repository requirements</h2>
                <span className="t-label">Required</span>
              </div>
              <pre className="bundles-structure">{`repo-root/
└── optional/subfolder/
    ├── module.py   # DSPy module + build_program()
    ├── metric.py   # judge_metric(example, prediction, trace=None)
    └── bundle.toml # name, version, score_pass_threshold`}</pre>
            </section>

            <section className="panel card-pad bundles-section">
              <h2 className="t-h2">Import checklist</h2>
              <ol className="bundles-checklist">
                {IMPORT_GUIDANCE.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <p className="cap" style={{ marginTop: 12 }}>
                New here? Download the example bundle, customize it locally, then push it to a GitHub repo before importing.
              </p>
            </section>

            <GitHubImportPanel modulesUrl={validateUrl} onBack={() => navigate("/bundles")} onCreatePlan={() => navigate("/plans?new=1")} />
          </>
        ) : <SavedBundlesPanel modulesUrl={validateUrl} />}
      </div>
    </section>
  );
}

function SavedBundlesPanel({ modulesUrl }) {
  const navigate = useNavigate();
  const [savedBundles, setSavedBundles] = useState([]);
  const [runHistoryByModule, setRunHistoryByModule] = useState({});
  const [isLoadingBundles, setIsLoadingBundles] = useState(false);
  const [syncingBundleId, setSyncingBundleId] = useState("");
  const [syncNotice, setSyncNotice] = useState(null);

  const loadBundles = async () => {
    setIsLoadingBundles(true);
    try {
      const [bundlesResponse, runsResponse] = await Promise.all([
        fetch(modulesUrl, { method: "GET" }),
        fetch(buildApiUrl("/agent-run-plans?limit=200&offset=0"), { method: "GET" }),
      ]);
      if (!bundlesResponse.ok) {
        throw new Error("Could not load bundles");
      }
      const payload = await bundlesResponse.json();
      const bundles = Array.isArray(payload) ? payload : [];
      setSavedBundles(bundles);
      if (runsResponse.ok) {
        const runsPayload = await runsResponse.json();
        setRunHistoryByModule(buildModuleRunHistory(Array.isArray(runsPayload) ? runsPayload : []));
      } else {
        setRunHistoryByModule({});
      }
    } catch {
      setSavedBundles([]);
      setRunHistoryByModule({});
    } finally {
      setIsLoadingBundles(false);
    }
  };

  const deleteBundle = async (bundleId) => {
    const response = await fetch(`${modulesUrl}/${bundleId}`, { method: "DELETE" });
    if (!response.ok) {
      return;
    }
    await loadBundles();
  };

  const syncBundle = async (bundle) => {
    if (!bundle?.id) {
      return;
    }
    setSyncingBundleId(bundle.id);
    setSyncNotice(null);
    try {
      const response = await fetch(`${modulesUrl}/${bundle.id}/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload?.error || `Could not sync bundle (${response.status})`);
      }
      await loadBundles();
      setSyncNotice({
        tone: "success",
        message: `${bundle.bundle_name || bundle.id} synced successfully.`,
      });
    } catch (error) {
      setSyncNotice({
        tone: "error",
        message: error instanceof Error ? error.message : "Could not sync bundle",
      });
    } finally {
      setSyncingBundleId("");
    }
  };

  useEffect(() => {
    loadBundles();
  }, []);

  return (
    <div className="panel card-pad bundles-validation-result">
      <div className="row between" style={{ marginBottom: 10 }}>
        <h3 className="t-h2">Saved bundles</h3>
        <Button size="sm" onClick={loadBundles} disabled={isLoadingBundles}>{isLoadingBundles ? "Refreshing..." : "Refresh"}</Button>
      </div>
      {syncNotice?.tone === "success" ? <div className="bundles-success-banner" role="status" style={{ marginBottom: 10 }}>{syncNotice.message}</div> : null}
      {syncNotice?.tone === "error" ? <div className="plans-validation-alert" role="alert" style={{ marginBottom: 10 }}><p className="plans-validation-copy">{syncNotice.message}</p></div> : null}
      {!savedBundles.length ? (
        <EmptyState title="No bundles saved yet" description="Import a GitHub repository to create your first tracked bundle." />
      ) : (
        <div className="col gap-2">
          {savedBundles.map((bundle) => (
            <div key={bundle.id} className="bundles-saved-row bundles-saved-row-bundle">
              <div className="bundles-saved-icon center">
                <Icon name="box" size={18} />
              </div>
              <div className="bundles-row-btn bundles-row-btn-bundle">
                <span className="t-sm">{bundle.bundle_name || bundle.github_repo_url || bundle.source_ref || bundle.id}</span>
                <span className="cap"><span className="mono">{bundle.validation_status}</span> · {bundle.status}</span>
                {bundle.bundle_version ? <span className="cap">v{bundle.bundle_version}</span> : null}
                {bundle.github_branch ? <span className="cap mono">Branch {bundle.github_branch}</span> : null}
                {bundle.github_subpath ? <span className="cap mono">Subfolder: {bundle.github_subpath}</span> : null}
                {bundle.current_commit_sha ? <span className="cap mono">Commit {bundle.current_commit_sha.slice(0, 8)}</span> : null}
                {bundle.created_at ? <span className="cap mono">Imported {formatDateTime(bundle.created_at)}</span> : null}
              </div>
              <BundleEvalSparkline bundle={bundle} history={runHistoryByModule[bundle.id] || []} />
              <div className="bundles-saved-actions">
                <Button size="sm" variant="primary" onClick={() => syncBundle(bundle)} disabled={syncingBundleId === bundle.id}>
                  {syncingBundleId === bundle.id ? "Syncing..." : "Sync"}
                </Button>
                <Button size="sm" onClick={() => {
                  navigate(`/bundles/${bundle.id}`);
                }}>Open</Button>
                <Button size="sm" className="bundles-delete-btn" onClick={() => deleteBundle(bundle.id)}>Delete</Button>
              </div>
            </div>
          ))}
        </div>
      )}

    </div>
  );
}

function BundleEvalSparkline({ bundle, history }) {
  const scoredHistory = Array.isArray(history) ? history.filter((item) => Number.isFinite(item?.average_score)) : [];
  if (!scoredHistory.length) {
    return (
      <div className="bundles-sparkline-card bundles-sparkline-card-empty">
        <span className="t-label">Eval trend</span>
        <span className="cap">No evals yet</span>
      </div>
    );
  }

  const recentHistory = scoredHistory.slice(-12);
  const latestScore = Number(recentHistory[recentHistory.length - 1].average_score);
  const threshold = recentHistory.findLast((item) => Number.isFinite(item?.score_pass_threshold))?.score_pass_threshold;
  const strokeClass = Number.isFinite(Number(threshold)) && latestScore < Number(threshold)
    ? "bundles-sparkline-line-fail"
    : "bundles-sparkline-line-pass";

  return (
    <div className="bundles-sparkline-card">
      <div className="row between bundles-sparkline-head">
        <span className="t-label">Eval trend</span>
        <span className={`mono bundles-sparkline-score ${strokeClass}`}>{Math.round(latestScore * 100)}%</span>
      </div>
      <SparklineChart points={recentHistory} threshold={threshold} label={`Recent eval scores for ${bundle.bundle_name || bundle.id}`} strokeClass={strokeClass} />
    </div>
  );
}

function SparklineChart({ points, threshold, label, strokeClass }) {
  const width = 170;
  const height = 42;
  const padding = 4;
  const values = points.map((item) => clampScore(Number(item.average_score)));
  const denom = Math.max(1, values.length - 1);
  const coordinates = values.map((value, index) => {
    const x = padding + ((width - padding * 2) * index) / denom;
    const y = padding + (1 - value) * (height - padding * 2);
    return [x, y];
  });
  const polylinePoints = coordinates.map(([x, y]) => `${x},${y}`).join(" ");
  const thresholdValue = Number.isFinite(Number(threshold)) ? clampScore(Number(threshold)) : null;
  const thresholdY = thresholdValue === null ? null : padding + (1 - thresholdValue) * (height - padding * 2);
  const title = points.map((item) => `${formatDateTime(item.created_at)}: ${Math.round(clampScore(Number(item.average_score)) * 100)}%`).join("\n");

  return (
    <svg className="bundles-sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <title>{title}</title>
      {thresholdY !== null ? <line className="bundles-sparkline-threshold" x1={padding} y1={thresholdY} x2={width - padding} y2={thresholdY} /> : null}
      <polyline className={`bundles-sparkline-line ${strokeClass}`} points={polylinePoints} />
      {coordinates.map(([x, y], index) => (
        <circle key={`${points[index].id || index}-${x}`} className={`bundles-sparkline-dot ${strokeClass}`} cx={x} cy={y} r="2.25" />
      ))}
    </svg>
  );
}

function buildModuleRunHistory(plans) {
  const grouped = {};
  plans.forEach((plan) => {
    if (!plan?.module_import_id) {
      return;
    }
    if (!grouped[plan.module_import_id]) {
      grouped[plan.module_import_id] = [];
    }
    grouped[plan.module_import_id].push(plan);
  });
  Object.keys(grouped).forEach((moduleId) => {
    grouped[moduleId] = grouped[moduleId]
      .slice()
      .sort((left, right) => String(left.created_at || "").localeCompare(String(right.created_at || "")));
  });
  return grouped;
}

function clampScore(value) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function BundleDetailPage({ moduleId, modulesUrl, onBack }) {
  const navigate = useNavigate();
  const [bundle, setBundle] = useState(null);
  const [bundleSync, setBundleSync] = useState(null);
  const [bundleRevisions, setBundleRevisions] = useState([]);
  const [bundleFiles, setBundleFiles] = useState({});
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingRevisions, setIsLoadingRevisions] = useState(false);
  const [detailTab, setDetailTab] = useState("details");
  const [activeFileName, setActiveFileName] = useState("module.py");
  const [editingName, setEditingName] = useState("");
  const [editingVersion, setEditingVersion] = useState("");
  const [environmentEntries, setEnvironmentEntries] = useState([]);
  const [metadataError, setMetadataError] = useState("");
  const [environmentError, setEnvironmentError] = useState("");
  const [syncActionError, setSyncActionError] = useState("");
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [isSavingEnvironment, setIsSavingEnvironment] = useState(false);
  const [isRefreshingSync, setIsRefreshingSync] = useState(false);
  const [isSyncingBundle, setIsSyncingBundle] = useState(false);

  const loadBundleFiles = async (bundleId) => {
    const response = await fetch(`${modulesUrl}/${bundleId}/files`, { method: "GET" });
    if (!response.ok) {
      return {};
    }
    const payload = await response.json();
    return payload && typeof payload === "object" ? payload : {};
  };

  const loadBundleSyncStatus = async (bundleId) => {
    const response = await fetch(`${modulesUrl}/${bundleId}/sync-status`, { method: "GET" });
    if (!response.ok) {
      return null;
    }
    return response.json();
  };

  const loadBundleRevisions = async (bundleId) => {
    setIsLoadingRevisions(true);
    try {
      const response = await fetch(`${modulesUrl}/${bundleId}/revisions`, { method: "GET" });
      if (!response.ok) {
        return [];
      }
      const payload = await response.json();
      return Array.isArray(payload) ? payload : [];
    } finally {
      setIsLoadingRevisions(false);
    }
  };

  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      try {
        const detailResp = await fetch(`${modulesUrl}/${moduleId}`, { method: "GET" });
        if (!detailResp.ok) {
          throw new Error(await parseError(detailResp, `Could not load bundle (${detailResp.status})`));
        }
        const detail = await detailResp.json();
        const [files, sync, revisions] = await Promise.all([
          loadBundleFiles(moduleId),
          loadBundleSyncStatus(moduleId),
          loadBundleRevisions(moduleId),
        ]);
        setBundle(detail);
        setBundleFiles(files);
        setBundleSync(sync);
        setBundleRevisions(revisions);
      } catch (error) {
        setMetadataError(error instanceof Error ? error.message : "Could not load bundle");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [moduleId, modulesUrl]);

  useEffect(() => {
    setEditingName(bundle?.bundle_name || "");
    setEditingVersion(bundle?.bundle_version || "");
    setEnvironmentEntries(Array.isArray(bundle?.environment_entries) ? bundle.environment_entries.map((entry) => ({
      key: entry?.key || "",
      value: entry?.value || "",
      is_secret: Boolean(entry?.is_secret),
    })) : []);
    setEnvironmentError(bundle?.environment_error || "");
  }, [bundle]);

  useEffect(() => {
    const fileNames = Object.keys(bundleFiles);
    if (fileNames.length && !fileNames.includes(activeFileName)) {
      setActiveFileName(fileNames[0]);
    }
  }, [bundleFiles, activeFileName]);

  const saveMetadata = async () => {
    if (!bundle?.id) return;
    setIsSavingMetadata(true);
    setMetadataError("");
    try {
      const response = await fetch(`${modulesUrl}/${bundle.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bundle_name: editingName, bundle_version: editingVersion }),
      });
      if (!response.ok) throw new Error(await parseError(response, `Could not update bundle (${response.status})`));
      setBundle(await response.json());
    } catch (error) {
      setMetadataError(error instanceof Error ? error.message : "Could not update bundle");
    } finally {
      setIsSavingMetadata(false);
    }
  };

  const saveEnvironment = async () => {
    if (!bundle?.id) return;
    setIsSavingEnvironment(true);
    setEnvironmentError("");
    try {
      const response = await fetch(`${modulesUrl}/${bundle.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          environment_entries: environmentEntries,
        }),
      });
      if (!response.ok) throw new Error(await parseError(response, `Could not save environment (${response.status})`));
      setBundle(await response.json());
    } catch (error) {
      setEnvironmentError(error instanceof Error ? error.message : "Could not save environment");
    } finally {
      setIsSavingEnvironment(false);
    }
  };

  const refreshSyncStatus = async () => {
    if (!bundle?.id) return;
    setIsRefreshingSync(true);
    setSyncActionError("");
    try {
      const response = await fetch(`${modulesUrl}/${bundle.id}/sync-status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error || `Could not refresh sync status (${response.status})`);
      setBundleSync(payload);
    } catch (error) {
      setSyncActionError(error instanceof Error ? error.message : "Could not refresh sync status");
    } finally {
      setIsRefreshingSync(false);
    }
  };

  const syncBundle = async () => {
    if (!bundle?.id) return;
    setIsSyncingBundle(true);
    setSyncActionError("");
    try {
      const response = await fetch(`${modulesUrl}/${bundle.id}/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error || `Could not sync bundle (${response.status})`);
      setBundleSync(payload);
      const detailResp = await fetch(`${modulesUrl}/${bundle.id}`, { method: "GET" });
      if (detailResp.ok) {
        setBundle(await detailResp.json());
      }
      setBundleRevisions(await loadBundleRevisions(bundle.id));
    } catch (error) {
      setSyncActionError(error instanceof Error ? error.message : "Could not sync bundle");
    } finally {
      setIsSyncingBundle(false);
    }
  };

  const updateEnvironmentEntry = (index, patch) => {
    setEnvironmentEntries((current) => current.map((entry, entryIndex) => (entryIndex === index ? { ...entry, ...patch } : entry)));
  };

  const addEnvironmentEntry = () => setEnvironmentEntries((current) => [...current, { key: "", value: "", is_secret: false }]);
  const removeEnvironmentEntry = (index) => setEnvironmentEntries((current) => current.filter((_, entryIndex) => entryIndex !== index));
  const handleEnvironmentPaste = (event) => {
    if (detailTab !== "environment") {
      return;
    }
    const pastedText = event.clipboardData?.getData("text") || "";
    const parsedEntries = parseEnvironmentPaste(pastedText);
    if (!parsedEntries.length) {
      return;
    }
    event.preventDefault();
    setEnvironmentEntries((current) => mergeEnvironmentEntries(current, parsedEntries));
  };

  if (isLoading) {
    return <section className="page"><div className="page-body bundles-wrap"><LoadingState label="Loading bundle..." /></div></section>;
  }

  if (!bundle) {
    return <section className="page"><div className="page-body bundles-wrap"><ErrorState title="Could not load bundle" description={metadataError || "Bundle not found"} /></div></section>;
  }

  return (
    <section className="page">
      <div className="page-body bundles-wrap">
        <header className="row between bundles-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>{bundle.bundle_name || bundle.github_repo_url || bundle.id}</h1>
            <p className="muted t-sm">Module detail and runtime configuration.</p>
          </div>
          <div className="row gap-2">
            <Button variant="ghost" onClick={onBack}>Back</Button>
            <Button variant="danger" onClick={async () => {
              const response = await fetch(`${modulesUrl}/${bundle.id}`, { method: "DELETE" });
              if (response.ok) navigate("/bundles");
            }}>Delete</Button>
          </div>
        </header>

        <div className="row gap-2" style={{ marginBottom: 14 }}>
          {["details", "sync", "validation", "files", "environment"].map((tab) => (
            <Button key={tab} size="sm" variant={detailTab === tab ? "primary" : "ghost"} onClick={() => setDetailTab(tab)}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </Button>
          ))}
        </div>

        {detailTab === "details" ? (
          <section className="panel card-pad bundles-section">
            <h2 className="t-h2" style={{ marginBottom: 10 }}>Details</h2>
            <p className="cap" style={{ marginBottom: 8 }}>ID: <span className="faint">{bundle.id}</span></p>
            {bundle.github_repo_url ? <p className="cap" style={{ marginBottom: 6 }}>Repository: <span className="faint">{bundle.github_repo_url}</span></p> : null}
            {bundle.github_branch ? <p className="cap" style={{ marginBottom: 6 }}>Branch: <span className="faint">{bundle.github_branch}</span></p> : null}
            {bundle.github_subpath ? <p className="cap" style={{ marginBottom: 6 }}>Subfolder: <span className="faint mono">{bundle.github_subpath}</span></p> : null}
            <div className="bundles-metadata-grid" style={{ marginTop: 12 }}>
              <label className="bundles-label" htmlFor="bundle-detail-name">Bundle name</label>
              <input id="bundle-detail-name" className="bundles-file-input" type="text" value={editingName} onChange={(event) => setEditingName(event.target.value)} />
              <label className="bundles-label" htmlFor="bundle-detail-version">Bundle version</label>
              <input id="bundle-detail-version" className="bundles-file-input" type="text" value={editingVersion} onChange={(event) => setEditingVersion(event.target.value)} />
            </div>
            <div className="row gap-2" style={{ marginTop: 10 }}>
              <Button size="sm" variant="primary" onClick={saveMetadata} disabled={isSavingMetadata}>{isSavingMetadata ? "Saving..." : "Save details"}</Button>
            </div>
            {metadataError ? <p className="cap" style={{ marginTop: 8 }}>{metadataError}</p> : null}
          </section>
        ) : null}

        {detailTab === "sync" ? (
          <section className="panel card-pad bundles-section">
            <h2 className="t-h2" style={{ marginBottom: 10 }}>Sync</h2>
            <p className="cap" style={{ marginBottom: 6 }}>Sync status: <span className="faint">{bundleSync?.sync_status || bundle.sync_status || "unknown"}</span></p>
            {bundle.current_commit_sha ? <p className="cap" style={{ marginBottom: 6 }}>Current commit: <span className="faint mono">{bundle.current_commit_sha}</span></p> : null}
            {bundleSync?.upstream_commit_sha ? <p className="cap" style={{ marginBottom: 6 }}>Upstream commit: <span className="faint mono">{bundleSync.upstream_commit_sha}</span></p> : null}
            <div className="row gap-2" style={{ margin: "10px 0" }}>
              <Button size="sm" onClick={refreshSyncStatus} disabled={isRefreshingSync || isSyncingBundle}>{isRefreshingSync ? "Refreshing..." : "Refresh sync status"}</Button>
              <Button size="sm" variant="primary" onClick={syncBundle} disabled={isRefreshingSync || isSyncingBundle}>{isSyncingBundle ? "Syncing..." : "Sync bundle"}</Button>
            </div>
            {syncActionError ? <ErrorState title="Bundle sync action failed" description={syncActionError} /> : null}
            <div className="t-h2" style={{ marginBottom: 8 }}>Revision history</div>
            {isLoadingRevisions ? <LoadingState label="Loading revisions..." /> : bundleRevisions.length ? (
              <ul className="bundles-diags">
                {bundleRevisions.slice(0, 8).map((revision) => (
                  <li key={revision.id}>
                    <span className="mono">{(revision.commit_sha || "unknown").slice(0, 8)}</span>
                    {" · "}
                    {revision.bundle_version ? `v${revision.bundle_version}` : "version unknown"}
                    {" · "}
                    {revision.source_event || "event unknown"}
                    {revision.created_at ? ` · ${formatDateTime(revision.created_at)}` : ""}
                  </li>
                ))}
              </ul>
            ) : <p className="cap">No revisions recorded yet.</p>}
          </section>
        ) : null}

        {detailTab === "validation" ? (
          <section className="panel card-pad bundles-section">
            <h2 className="t-h2" style={{ marginBottom: 10 }}>Validation</h2>
            <p className="cap" style={{ marginBottom: 6 }}>Validation status: <span className="faint">{bundle.validation_status}</span></p>
            <div className="bundles-check-report" style={{ marginBottom: 12 }}>
              <div className="t-h2" style={{ marginBottom: 8 }}>Validation checklist</div>
              <ul className="bundles-check-results">
                {buildValidationChecks(bundle).map((check) => (
                  <li key={`${bundle.id}-${check.id}`} className="bundles-check-item">
                    <span aria-hidden="true">{check.passed ? "✅" : "❌"}</span>
                    <span>{check.label}</span>
                  </li>
                ))}
              </ul>
            </div>
            {normalizeDiagnostics(bundle.diagnostics).length ? (
              <ul className="bundles-diags" style={{ marginTop: 12 }}>
                {normalizeDiagnostics(bundle.diagnostics).map((diag, idx) => (
                  <li key={`${bundle.id}-${diag.code}-${idx}`}>{diag.code}: {diag.message}</li>
                ))}
              </ul>
            ) : null}
          </section>
        ) : null}

        {detailTab === "files" ? (
          <section className="panel card-pad bundles-section">
            <h2 className="t-h2" style={{ marginBottom: 10 }}>Files</h2>
            <div className="row gap-2" style={{ marginBottom: 10 }}>
              {Object.keys(bundleFiles).length ? Object.keys(bundleFiles).map((fileName) => (
                <Button key={`${bundle.id}-${fileName}`} size="sm" variant={activeFileName === fileName ? "primary" : "ghost"} onClick={() => setActiveFileName(fileName)}>{fileName}</Button>
              )) : Object.keys(BUNDLE_FILE_TEMPLATES).map((fileName) => (
                <Button key={`${bundle.id}-${fileName}`} size="sm" variant={activeFileName === fileName ? "primary" : "ghost"} onClick={() => setActiveFileName(fileName)}>{fileName}</Button>
              ))}
            </div>
            <pre className="bundles-snippet"><code>{renderHighlightedPython(bundleFiles[activeFileName] || BUNDLE_FILE_TEMPLATES[activeFileName] || "# file not available")}</code></pre>
          </section>
        ) : null}

        {detailTab === "environment" ? (
          <section className="panel card-pad bundles-section" onPaste={handleEnvironmentPaste}>
            <h2 className="t-h2" style={{ marginBottom: 10 }}>Environment</h2>
            <div className="bundles-warning" style={{ marginBottom: 12 }}>
              Sensitive information stored here is dangerous even with encryption at rest. Lock this server down with strict network rules and other host-level protections before using secret values.
            </div>
            <p className="cap" style={{ marginBottom: 12 }}>Paste dotenv-style lines anywhere in this tab to populate the editor automatically.</p>
            <div className="bundles-environment-list">
              {environmentEntries.map((entry, index) => (
                <div key={`${bundle.id}-env-${index}`} className="bundles-environment-row">
                  <input aria-label={`Environment key ${index + 1}`} className="bundles-file-input" type="text" placeholder="ENV_KEY" value={entry.key} onChange={(event) => updateEnvironmentEntry(index, { key: event.target.value.toUpperCase() })} />
                  <input aria-label={`Environment value ${index + 1}`} className="bundles-file-input" type={entry.is_secret ? "password" : "text"} placeholder="value" value={entry.value} onChange={(event) => updateEnvironmentEntry(index, { value: event.target.value })} />
                  <label className="bundles-environment-secret-toggle">
                    <input type="checkbox" checked={entry.is_secret} onChange={(event) => updateEnvironmentEntry(index, { is_secret: event.target.checked })} />
                    Secret
                  </label>
                  <Button size="sm" variant="danger" onClick={() => removeEnvironmentEntry(index)}>Remove</Button>
                </div>
              ))}
            </div>
            <div className="row gap-2" style={{ marginTop: 10 }}>
              <Button size="sm" onClick={addEnvironmentEntry}>Add variable</Button>
              <Button size="sm" variant="primary" onClick={saveEnvironment} disabled={isSavingEnvironment}>{isSavingEnvironment ? "Saving..." : "Save environment"}</Button>
            </div>
            {environmentError ? <p className="cap" style={{ marginTop: 8 }}>{environmentError}</p> : null}
          </section>
        ) : null}

      </div>
    </section>
  );
}

function normalizeDiagnostics(value) {
  if (Array.isArray(value)) {
    return value;
  }
  if (!value || typeof value !== "object") {
    return [];
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

async function parseError(response, fallback) {
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

function GitHubImportPanel({ modulesUrl, onBack, onCreatePlan }) {
  const [githubRepoUrl, setGithubRepoUrl] = useState("");
  const [githubBranch, setGithubBranch] = useState("main");
  const [githubSubpath, setGithubSubpath] = useState("");
  const [githubConfigured, setGithubConfigured] = useState(null);
  const [githubConfigMessage, setGithubConfigMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState(null);
  const [activeHelpCheck, setActiveHelpCheck] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadReadyState = async () => {
      try {
        const response = await fetch(buildApiUrl("/ready"), { method: "GET" });
        const payload = await response.json();
        if (cancelled) {
          return;
        }
        const configured = Boolean(payload?.github?.configured);
        setGithubConfigured(configured);
        setGithubConfigMessage(
          configured
            ? "GitHub access is configured in the backend environment."
            : "GitHub access is not configured. Set GITHUB_PAT or DSPY_TRAINER_GITHUB_PAT in .env, then restart backend and worker containers.",
        );
      } catch {
        if (!cancelled) {
          setGithubConfigured(false);
          setGithubConfigMessage("Could not verify GitHub access configuration from the backend.");
        }
      }
    };
    loadReadyState();
    return () => {
      cancelled = true;
    };
  }, []);

  const onSubmit = async (event) => {
    event.preventDefault();
    if (!githubConfigured) {
      setSubmitError("GitHub access is not configured in the backend environment.");
      return;
    }
    if (!githubRepoUrl.trim() || !githubBranch.trim()) {
      setSubmitError("Repository URL and branch are required.");
      return;
    }
    setIsSubmitting(true);
    setSubmitError("");
    setResult(null);
    try {
      const importResp = await fetch(`${modulesUrl}/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: "github",
          github_repo_url: githubRepoUrl.trim(),
          github_branch: githubBranch.trim(),
          github_subpath: githubSubpath.trim() || null,
        }),
      });
      if (!importResp.ok) {
        throw new Error(await parseError(importResp, `Import failed (${importResp.status})`));
      }
      const imported = await importResp.json();

      const detailResp = await fetch(`${modulesUrl}/${imported.id}`, { method: "GET" });
      if (!detailResp.ok) {
        throw new Error(await parseError(detailResp, `Could not load imported bundle (${detailResp.status})`));
      }
      const detail = await detailResp.json();
      setResult({
        moduleId: imported.id,
        validation_status: detail.validation_status,
        diagnostics: normalizeDiagnostics(detail.diagnostics),
        bundle_name: detail.bundle_name,
        bundle_version: detail.bundle_version,
        github_repo_url: detail.github_repo_url,
        github_branch: detail.github_branch,
        github_subpath: detail.github_subpath,
        current_commit_sha: detail.current_commit_sha,
      });
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Import failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="panel card-pad bundles-section">
      <div className="row between" style={{ marginBottom: 10 }}>
        <h2 className="t-h2">Step 2: Import and validate GitHub bundle</h2>
        <Button variant="ghost" size="sm" onClick={onBack}>Back</Button>
      </div>
      <p className="cap" style={{ marginBottom: 14 }}>Import a GitHub repository and optionally point at a subfolder that matches the bundle contract. GitHub access is configured server-side only.</p>
      {githubConfigMessage ? (
        githubConfigured ? (
          <div className="field-help row gap-2" role="status" style={{ marginBottom: 14 }}>
            <Icon name="info" size={13} className="field-help-icon" />
            <span className="cap field-help-copy">{githubConfigMessage}</span>
          </div>
        ) : (
          <div style={{ marginBottom: 14 }}>
            <ErrorState title="GitHub access not configured" description={githubConfigMessage} />
          </div>
        )
      ) : null}

      <form className="bundles-form col gap-2" onSubmit={onSubmit}>
        <label className="bundles-label" htmlFor="github-repo-url">GitHub repository URL</label>
        <input
          id="github-repo-url"
          className="bundles-file-input"
          type="url"
          placeholder="https://github.com/owner/repo"
          value={githubRepoUrl}
          onChange={(event) => setGithubRepoUrl(event.target.value)}
          required
        />

        <label className="bundles-label" htmlFor="github-branch">Branch</label>
        <input
          id="github-branch"
          className="bundles-file-input"
          type="text"
          placeholder="main"
          value={githubBranch}
          onChange={(event) => setGithubBranch(event.target.value)}
          required
        />

        <label className="bundles-label" htmlFor="github-subpath">Bundle subfolder (optional)</label>
        <input
          id="github-subpath"
          className="bundles-file-input"
          type="text"
          placeholder="e.g. bundles/support-triage"
          value={githubSubpath}
          onChange={(event) => setGithubSubpath(event.target.value)}
        />

        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn btn-primary" type="submit" disabled={isSubmitting || !githubConfigured || !githubRepoUrl.trim() || !githubBranch.trim()}>{isSubmitting ? "Importing..." : "Import + validate"}</button>
        </div>
      </form>

      {isSubmitting ? <LoadingState label="Cloning repository and running validation..." /> : null}
      {submitError ? <ErrorState title="Import failed" description={submitError} /> : null}

      {result ? (
        <div className="panel card-pad bundles-validation-result">
          <div className="row between" style={{ marginBottom: 10 }}>
            <h3 className="t-h2">Validation result</h3>
            <span className="t-label">{result.validation_status}</span>
          </div>
          <p className="cap" style={{ marginBottom: 10 }}>Module ID: <span className="faint">{result.moduleId}</span></p>
          {result.github_repo_url ? <p className="cap" style={{ marginBottom: 6 }}>Repository: <span className="faint">{result.github_repo_url}</span></p> : null}
          {result.github_branch ? <p className="cap" style={{ marginBottom: 6 }}>Branch: <span className="faint">{result.github_branch}</span></p> : null}
          <p className="cap" style={{ marginBottom: 6 }}>Subfolder: <span className="faint mono">{result.github_subpath || "."}</span></p>
          {result.current_commit_sha ? <p className="cap" style={{ marginBottom: 10 }}>Commit: <span className="faint mono">{result.current_commit_sha}</span></p> : null}
          <div className="bundles-check-report" style={{ marginBottom: 12 }}>
            <div className="t-label" style={{ marginBottom: 8 }}>Validation checks</div>
            <ul className="bundles-check-results">
              {buildValidationChecks(result).map((check) => (
                <li key={check.id} className="bundles-check-item">
                  <button className="bundles-info-btn" type="button" onClick={() => setActiveHelpCheck(check)} aria-label={`How to pass ${check.label}`}>
                    i
                  </button>
                  <span aria-hidden="true">{check.passed ? "✅" : "❌"}</span>
                  <span>{check.label}</span>
                </li>
              ))}
            </ul>
          </div>

          {result.diagnostics?.length ? (
            <ul className="bundles-diags">
              {result.diagnostics.map((diag, idx) => (
                <li key={`${diag.code}-${idx}`}>
                  <span className="t-label">{diag.level || diag.severity || "info"}</span> {diag.code}: {diag.message}
                </li>
              ))}
            </ul>
          ) : (
            <div className="state-card col center bundles-no-diag-card">
              <p className="t-h2">No diagnostics</p>
              <p className="muted">Validation passed without warnings.</p>
              <hr className="hr bundles-no-diag-hr" />
              {result.validation_status === "passed" ? (
                <Button className="bundles-plan-cta" variant="primary" size="lg" onClick={onCreatePlan}>Create Evaluation Plan</Button>
              ) : null}
            </div>
          )}

          {result.validation_status === "passed" && result.diagnostics?.length ? (
            <div className="bundles-success-wrap">
              <div className="bundles-success-banner" role="status">Bundle validated and saved successfully.</div>
              <Button className="bundles-plan-cta" variant="primary" size="lg" onClick={onCreatePlan}>Create Evaluation Plan</Button>
            </div>
          ) : null}
        </div>
      ) : null}

      {activeHelpCheck ? (
        <div className="bundles-modal-backdrop" onClick={() => setActiveHelpCheck(null)}>
          <div className="bundles-modal panel card-pad" role="dialog" aria-modal="true" aria-label={`${activeHelpCheck.label} guidance`} onClick={(event) => event.stopPropagation()}>
            <div className="row between" style={{ marginBottom: 10 }}>
              <h3 className="t-h2">{activeHelpCheck.label}</h3>
              <button className="btn btn-ghost btn-sm" type="button" onClick={() => setActiveHelpCheck(null)}>Close</button>
            </div>
            <p className="t-sm muted">{activeHelpCheck.help}</p>
            {activeHelpCheck.snippet ? (
              <div className="bundles-snippet-wrap">
                <div className="row between" style={{ marginBottom: 8 }}>
                  <span className="t-label">Starter template</span>
                  <button
                    className="btn btn-sm"
                    type="button"
                    onClick={async () => {
                      await navigator.clipboard.writeText(activeHelpCheck.snippet);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1000);
                    }}
                  >
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
                <pre className="bundles-snippet"><code>{renderHighlightedPython(activeHelpCheck.snippet)}</code></pre>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function buildValidationChecks(result) {
  const codes = new Set(normalizeDiagnostics(result?.diagnostics).map((diag) => diag.code));
  return VALIDATION_CHECKS.map((check) => ({
    ...check,
    passed: check.failCodes.every((code) => !codes.has(code)),
  }));
}

function renderHighlightedPython(source) {
  const tokenRegex = /(#[^\n]*|"""[\s\S]*?"""|"[^"\n]*"|'[^'\n]*'|\b(?:class|def|return|import|from|if|and|or|True|False|None)\b)/g;
  const nodes = [];
  let lastIndex = 0;
  let match;

  while ((match = tokenRegex.exec(source)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(source.slice(lastIndex, match.index));
    }
    const token = match[0];
    const cls = token.startsWith("#") ? "tok-com" : token.startsWith("\"") || token.startsWith("'") ? "tok-str" : "tok-kw";
    nodes.push(<span key={`${match.index}-${token.length}`} className={cls}>{token}</span>);
    lastIndex = tokenRegex.lastIndex;
  }

  if (lastIndex < source.length) {
    nodes.push(source.slice(lastIndex));
  }

  return nodes;
}
