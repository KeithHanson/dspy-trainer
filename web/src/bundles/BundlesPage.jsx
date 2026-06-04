import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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

export function BundlesPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const showImportIntent = searchParams.get("import") === "1";

  const sampleUrl = useMemo(() => buildApiUrl("/samples/module-bundle"), []);
  const validateUrl = useMemo(() => buildApiUrl("/modules"), []);

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
  const [savedBundles, setSavedBundles] = useState([]);
  const [isLoadingBundles, setIsLoadingBundles] = useState(false);
  const [selectedBundle, setSelectedBundle] = useState(null);
  const [selectedBundleSync, setSelectedBundleSync] = useState(null);
  const [bundleRevisions, setBundleRevisions] = useState([]);
  const [isLoadingRevisions, setIsLoadingRevisions] = useState(false);
  const [syncActionError, setSyncActionError] = useState("");
  const [isRefreshingSync, setIsRefreshingSync] = useState(false);
  const [isSyncingBundle, setIsSyncingBundle] = useState(false);
  const [activeFileName, setActiveFileName] = useState("module.py");
  const [bundleFiles, setBundleFiles] = useState({});
  const [editingName, setEditingName] = useState("");
  const [editingVersion, setEditingVersion] = useState("");
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [metadataError, setMetadataError] = useState("");
  const [metadataModalBundle, setMetadataModalBundle] = useState(null);

  const loadBundleFiles = async (bundleId) => {
    if (!bundleId) {
      setBundleFiles({});
      return {};
    }
    try {
      const response = await fetch(`${modulesUrl}/${bundleId}/files`, { method: "GET" });
      if (!response.ok) {
        setBundleFiles({});
        return {};
      }
      const payload = await response.json();
      const files = payload && typeof payload === "object" ? payload : {};
      setBundleFiles(files);
      return files;
    } catch {
      setBundleFiles({});
      return {};
    }
  };

  const loadBundleSyncStatus = async (bundleId) => {
    if (!bundleId) {
      setSelectedBundleSync(null);
      return null;
    }
    try {
      const response = await fetch(`${modulesUrl}/${bundleId}/sync-status`, { method: "GET" });
      if (!response.ok) {
        setSelectedBundleSync(null);
        return null;
      }
      const payload = await response.json();
      setSelectedBundleSync(payload);
      return payload;
    } catch {
      setSelectedBundleSync(null);
      return null;
    }
  };

  const loadBundleRevisions = async (bundleId) => {
    if (!bundleId) {
      setBundleRevisions([]);
      return [];
    }
    setIsLoadingRevisions(true);
    try {
      const response = await fetch(`${modulesUrl}/${bundleId}/revisions`, { method: "GET" });
      if (!response.ok) {
        setBundleRevisions([]);
        return [];
      }
      const payload = await response.json();
      const revisions = Array.isArray(payload) ? payload : [];
      setBundleRevisions(revisions);
      return revisions;
    } catch {
      setBundleRevisions([]);
      return [];
    } finally {
      setIsLoadingRevisions(false);
    }
  };

  const loadBundles = async () => {
    setIsLoadingBundles(true);
    try {
      const response = await fetch(modulesUrl, { method: "GET" });
      if (!response.ok) {
        throw new Error("Could not load bundles");
      }
      const payload = await response.json();
      const bundles = Array.isArray(payload) ? payload : [];
      setSavedBundles(bundles);
      if (selectedBundle) {
        const refreshed = bundles.find((item) => item.id === selectedBundle.id);
        setSelectedBundle(refreshed || null);
      }
    } catch {
      setSavedBundles([]);
    } finally {
      setIsLoadingBundles(false);
    }
  };

  const deleteBundle = async (bundleId) => {
    const response = await fetch(`${modulesUrl}/${bundleId}`, { method: "DELETE" });
    if (!response.ok) {
      return;
    }
    if (selectedBundle?.id === bundleId) {
      setSelectedBundle(null);
    }
    await loadBundles();
  };

  useEffect(() => {
    loadBundles();
  }, []);

  useEffect(() => {
    const source = metadataModalBundle || selectedBundle;
    setEditingName(source?.bundle_name || "");
    setEditingVersion(source?.bundle_version || "");
    setMetadataError("");
  }, [selectedBundle?.id, selectedBundle?.bundle_name, selectedBundle?.bundle_version, metadataModalBundle?.id, metadataModalBundle?.bundle_name, metadataModalBundle?.bundle_version]);

  useEffect(() => {
    const loadFiles = async () => {
      if (!selectedBundle?.id) {
        setBundleFiles({});
        setSelectedBundleSync(null);
        setBundleRevisions([]);
        return;
      }
      setSyncActionError("");
      await Promise.all([
        loadBundleFiles(selectedBundle.id),
        loadBundleSyncStatus(selectedBundle.id),
        loadBundleRevisions(selectedBundle.id),
      ]);
    };
    loadFiles();
  }, [modulesUrl, selectedBundle?.id]);

  useEffect(() => {
    const fileNames = Object.keys(bundleFiles);
    if (!fileNames.length) {
      return;
    }
    if (!fileNames.includes(activeFileName)) {
      setActiveFileName(fileNames[0]);
    }
  }, [bundleFiles, activeFileName]);

  const saveBundleMetadata = async () => {
    const targetBundle = metadataModalBundle || selectedBundle;
    if (!targetBundle?.id) {
      return;
    }
    setIsSavingMetadata(true);
    setMetadataError("");
    try {
      const response = await fetch(`${modulesUrl}/${targetBundle.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bundle_name: editingName,
          bundle_version: editingVersion,
        }),
      });
      if (!response.ok) {
        throw new Error(await parseError(response, `Could not update bundle (${response.status})`));
      }
      const updated = await response.json();
      setSavedBundles((current) => current.map((bundle) => (bundle.id === updated.id ? updated : bundle)));
      if (selectedBundle?.id === updated.id) {
        setSelectedBundle(updated);
      }
      setMetadataModalBundle(null);
    } catch (error) {
      setMetadataError(error instanceof Error ? error.message : "Could not update bundle");
    } finally {
      setIsSavingMetadata(false);
    }
  };

  const openBundleFile = async (fileName) => {
    if (!selectedBundle?.id) {
      setActiveFileName(fileName);
      return;
    }
    const files = await loadBundleFiles(selectedBundle.id);
    setActiveFileName(Object.prototype.hasOwnProperty.call(files, fileName) ? fileName : fileName);
  };

  const refreshSelectedBundleGitState = async () => {
    if (!selectedBundle?.id) {
      return;
    }
    setIsRefreshingSync(true);
    setSyncActionError("");
    try {
      const response = await fetch(`${modulesUrl}/${selectedBundle.id}/sync-status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload?.error || `Could not refresh sync status (${response.status})`);
      }
      setSelectedBundleSync(payload);
      await loadBundles();
      await loadBundleRevisions(selectedBundle.id);
    } catch (error) {
      setSyncActionError(error instanceof Error ? error.message : "Could not refresh sync status");
    } finally {
      setIsRefreshingSync(false);
    }
  };

  const syncSelectedBundle = async () => {
    if (!selectedBundle?.id) {
      return;
    }
    setIsSyncingBundle(true);
    setSyncActionError("");
    try {
      const response = await fetch(`${modulesUrl}/${selectedBundle.id}/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload?.error || `Could not sync bundle (${response.status})`);
      }
      setSelectedBundleSync(payload);
      await loadBundles();
      await loadBundleRevisions(selectedBundle.id);
    } catch (error) {
      setSyncActionError(error instanceof Error ? error.message : "Could not sync bundle");
    } finally {
      setIsSyncingBundle(false);
    }
  };

  return (
    <div className="panel card-pad bundles-validation-result">
      <div className="row between" style={{ marginBottom: 10 }}>
        <h3 className="t-h2">Saved bundles</h3>
        <Button size="sm" onClick={loadBundles} disabled={isLoadingBundles}>{isLoadingBundles ? "Refreshing..." : "Refresh"}</Button>
      </div>
      {!savedBundles.length ? (
        <EmptyState title="No bundles saved yet" description="Import a GitHub repository to create your first tracked bundle." />
      ) : (
        <div className="col gap-2">
          {savedBundles.map((bundle) => (
            <div key={bundle.id} className="bundles-saved-row">
              <div className="bundles-saved-icon center">
                <Icon name="box" size={18} />
              </div>
              <div className="bundles-row-btn">
                <span className="t-sm">{bundle.bundle_name || bundle.github_repo_url || bundle.source_ref || bundle.id}</span>
                <span className="cap"><span className="mono">{bundle.validation_status}</span> · {bundle.status}</span>
                {bundle.bundle_version ? <span className="cap">v{bundle.bundle_version}</span> : null}
                {bundle.github_branch ? <span className="cap mono">Branch {bundle.github_branch}</span> : null}
                {bundle.github_subpath ? <span className="cap mono">Subfolder: {bundle.github_subpath}</span> : null}
                {bundle.current_commit_sha ? <span className="cap mono">Commit {bundle.current_commit_sha.slice(0, 8)}</span> : null}
                {bundle.created_at ? <span className="cap mono">Imported {formatDateTime(bundle.created_at)}</span> : null}
              </div>
              <Button size="sm" onClick={() => {
                setActiveFileName("module.py");
                setSelectedBundle(bundle);
              }}>View files</Button>
              <Button size="sm" className="bundles-delete-btn" onClick={() => deleteBundle(bundle.id)}>Delete</Button>
            </div>
          ))}
        </div>
      )}

      {metadataModalBundle ? (
        <div className="bundles-modal-backdrop" onClick={() => setMetadataModalBundle(null)}>
          <div className="panel card-pad bundles-modal" onClick={(event) => event.stopPropagation()}>
            <div className="row between" style={{ marginBottom: 8 }}>
              <h4 className="t-h2">Edit bundle metadata</h4>
              <Button size="sm" variant="ghost" onClick={() => setMetadataModalBundle(null)}>Close</Button>
            </div>
            <p className="cap" style={{ marginBottom: 8 }}>ID: <span className="faint">{metadataModalBundle.id}</span></p>
            <div className="bundles-metadata-form" style={{ marginBottom: 12 }}>
              <div className="bundles-metadata-grid">
                <label className="bundles-label" htmlFor="bundle-name-input">Bundle name</label>
                <input id="bundle-name-input" className="bundles-file-input" type="text" value={editingName} onChange={(event) => setEditingName(event.target.value)} />
                <label className="bundles-label" htmlFor="bundle-version-input">Bundle version</label>
                <input id="bundle-version-input" className="bundles-file-input" type="text" value={editingVersion} onChange={(event) => setEditingVersion(event.target.value)} />
              </div>
              <div className="row gap-2" style={{ marginTop: 10 }}>
                <Button size="sm" variant="primary" onClick={saveBundleMetadata} disabled={isSavingMetadata}>{isSavingMetadata ? "Saving..." : "Save metadata"}</Button>
              </div>
              {metadataError ? <p className="cap" style={{ marginTop: 8 }}>{metadataError}</p> : null}
            </div>
          </div>
        </div>
      ) : null}

      {selectedBundle ? (
        <div className="panel card-pad bundles-validation-result">
          <div className="row between" style={{ marginBottom: 8 }}>
            <h4 className="t-h2">Bundle detail</h4>
          </div>
          <p className="cap" style={{ marginBottom: 8 }}>ID: <span className="faint">{selectedBundle.id}</span></p>
          {selectedBundle.github_repo_url ? (
            <div className="col gap-1" style={{ marginBottom: 12 }}>
              <p className="cap">Repository: <span className="faint">{selectedBundle.github_repo_url}</span></p>
              <p className="cap">Branch: <span className="faint">{selectedBundle.github_branch || "unknown"}</span></p>
              <p className="cap">Subfolder: <span className="faint mono">{selectedBundle.github_subpath || "."}</span></p>
              <p className="cap">Sync status: <span className="faint">{selectedBundleSync?.sync_status || selectedBundle.sync_status || "unknown"}</span></p>
              {selectedBundle.current_commit_sha ? <p className="cap">Current commit: <span className="faint mono">{selectedBundle.current_commit_sha}</span></p> : null}
              {selectedBundleSync?.upstream_commit_sha ? <p className="cap">Upstream commit: <span className="faint mono">{selectedBundleSync.upstream_commit_sha}</span></p> : null}
              {selectedBundle.last_synced_at ? <p className="cap">Last synced: <span className="faint">{formatDateTime(selectedBundle.last_synced_at)}</span></p> : null}
              <div className="row gap-2" style={{ marginTop: 8 }}>
                <Button size="sm" onClick={refreshSelectedBundleGitState} disabled={isRefreshingSync || isSyncingBundle}>
                  {isRefreshingSync ? "Refreshing..." : "Refresh sync status"}
                </Button>
                <Button size="sm" variant="primary" onClick={syncSelectedBundle} disabled={isRefreshingSync || isSyncingBundle}>
                  {isSyncingBundle ? "Syncing..." : "Sync bundle"}
                </Button>
              </div>
            </div>
          ) : null}
          {syncActionError ? <ErrorState title="Bundle sync action failed" description={syncActionError} /> : null}
          {selectedBundle.github_repo_url ? (
            <div className="bundles-check-detail-list" style={{ marginBottom: 12 }}>
              <div className="t-h2" style={{ marginBottom: 8 }}>Revision history</div>
              {isLoadingRevisions ? (
                <LoadingState label="Loading revisions..." />
              ) : bundleRevisions.length ? (
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
              ) : (
                <p className="cap">No revisions recorded yet.</p>
              )}
            </div>
          ) : null}
          <div className="bundles-check-report" style={{ marginBottom: 12 }}>
            <div className="t-h2" style={{ marginBottom: 8 }}>Validation checklist</div>
            <ul className="bundles-check-results">
              {buildValidationChecks(selectedBundle).map((check) => (
                <li key={`${selectedBundle.id}-${check.id}`} className="bundles-check-item">
                  <span aria-hidden="true">{check.passed ? "✅" : "❌"}</span>
                  <span>{check.label}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="bundles-check-detail-list" style={{ marginBottom: 12 }}>
            <div className="t-h2" style={{ marginBottom: 8 }}>Bundle files</div>
            <p className="cap" style={{ marginBottom: 8 }}>Source file preview from the validation view.</p>
            <hr className="hr" style={{ marginBottom: 10 }} />
            <div className="row gap-2" style={{ marginBottom: 10 }}>
              {Object.keys(bundleFiles).length ? Object.keys(bundleFiles).map((fileName) => (
                <Button key={`${selectedBundle.id}-${fileName}`} size="sm" variant={activeFileName === fileName ? "primary" : "ghost"} onClick={() => openBundleFile(fileName)}>
                  {fileName}
                </Button>
              )) : Object.keys(BUNDLE_FILE_TEMPLATES).map((fileName) => (
                <Button key={`${selectedBundle.id}-${fileName}`} size="sm" variant={activeFileName === fileName ? "primary" : "ghost"} onClick={() => openBundleFile(fileName)}>
                  {fileName}
                </Button>
              ))}
            </div>
            <pre className="bundles-snippet"><code>{renderHighlightedPython(bundleFiles[activeFileName] || BUNDLE_FILE_TEMPLATES[activeFileName] || "# file not available")}</code></pre>
          </div>

          {normalizeDiagnostics(selectedBundle.diagnostics).length ? (
            <ul className="bundles-diags">
              {normalizeDiagnostics(selectedBundle.diagnostics).map((diag, idx) => (
                <li key={`${selectedBundle.id}-${diag.code}-${idx}`}>{diag.code}: {diag.message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
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
