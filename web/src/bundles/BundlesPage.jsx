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
lm_target = "gpt-4.1-mini"
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
    failCodes: ["bundle_toml_invalid", "bundle_toml_name_missing", "bundle_toml_version_missing", "bundle_toml_lm_target_missing", "bundle_toml_score_pass_threshold_invalid"],
    help: "bundle.toml must be valid TOML and include non-empty name/version/lm_target plus numeric score_pass_threshold between 0.0 and 1.0.",
    snippet: `name = "support-triage-agent"
version = "0.1.0"
lm_target = "gpt-4.1-mini"
score_pass_threshold = 0.8
`,
  },
];

function buildApiUrl(path) {
  const base = import.meta.env.VITE_API_BASE_URL?.trim();
  if (!base) return path;
  return `${base.replace(/\/$/, "")}${path}`;
}

const PREP_STEPS = [
  "Download the sample archive and unzip it locally.",
  "Copy the sample folder and rename it for your module.",
  "Update module.py with your DSPy signature and module class.",
  "Update metric.py with your evaluation contract.",
  "Set bundle.toml name/version/lm_target and score_pass_threshold.",
  "Zip the folder root, then upload for validation.",
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
  const showUploadIntent = searchParams.get("upload") === "1";

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
            <p className="muted t-sm">Agent code packages - <span className="mono">module.py</span> + <span className="mono">metric.py</span> - validated in a sandbox.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={handleDownload} disabled={isDownloading}>
              {isDownloading ? "Downloading..." : "Example bundle"}
            </Button>
            <Button variant="primary" onClick={() => navigate("/bundles?upload=1")}>Upload bundle</Button>
          </div>
        </header>

        {downloadError ? <ErrorState title="Download failed" description={downloadError} /> : null}

        {showUploadIntent ? (
          <>
            <section className="panel card-pad bundles-section">
              <div className="row between">
                <h2 className="t-h2">Expected bundle structure</h2>
                <span className="t-label">Required</span>
              </div>
              <pre className="bundles-structure">{`example-bundle.zip
├── module.py   # DSPy module + build_program()
├── metric.py   # judge_metric(example, prediction, trace=None)
└── bundle.toml # name, version, lm_target`}</pre>
            </section>

            <section className="panel card-pad bundles-section">
              <h2 className="t-h2">Preparation checklist</h2>
              <ol className="bundles-checklist">
                {PREP_STEPS.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </section>

            <UploadValidatePanel modulesUrl={validateUrl} onBack={() => navigate("/bundles")} onCreatePlan={() => navigate("/plans?new=1")} />
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
  const [activeFileName, setActiveFileName] = useState("module.py");
  const [bundleFiles, setBundleFiles] = useState({});
  const [editingName, setEditingName] = useState("");
  const [editingVersion, setEditingVersion] = useState("");
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [metadataError, setMetadataError] = useState("");

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
    setEditingName(selectedBundle?.bundle_name || "");
    setEditingVersion(selectedBundle?.bundle_version || "");
    setMetadataError("");
  }, [selectedBundle?.id, selectedBundle?.bundle_name, selectedBundle?.bundle_version]);

  useEffect(() => {
    const loadFiles = async () => {
      if (!selectedBundle?.id) {
        setBundleFiles({});
        return;
      }
      await loadBundleFiles(selectedBundle.id);
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
    if (!selectedBundle?.id) {
      return;
    }
    setIsSavingMetadata(true);
    setMetadataError("");
    try {
      const response = await fetch(`${modulesUrl}/${selectedBundle.id}`, {
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
      setSelectedBundle(updated);
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

  return (
    <div className="panel card-pad bundles-validation-result">
      <div className="row between" style={{ marginBottom: 10 }}>
        <h3 className="t-h2">Saved bundles</h3>
        <Button size="sm" onClick={loadBundles} disabled={isLoadingBundles}>{isLoadingBundles ? "Refreshing..." : "Refresh"}</Button>
      </div>
      {!savedBundles.length ? (
        <EmptyState title="No bundles saved yet" description="Upload and validate a bundle to save it." />
      ) : (
        <div className="col gap-2">
          {savedBundles.map((bundle) => (
            <div key={bundle.id} className="bundles-saved-row">
              <div className="bundles-saved-icon center">
                <Icon name="box" size={18} />
              </div>
              <div className="bundles-row-btn">
                <span className="t-sm">{bundle.bundle_name || bundle.source_ref || bundle.id}</span>
                <span className="cap"><span className="mono">{bundle.validation_status}</span> · {bundle.status}</span>
                {bundle.bundle_version ? <span className="cap">v{bundle.bundle_version}</span> : null}
                {bundle.created_at ? <span className="cap mono">Uploaded {formatDateTime(bundle.created_at)}</span> : null}
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

      {selectedBundle ? (
        <div className="panel card-pad bundles-validation-result">
          <div className="row between" style={{ marginBottom: 8 }}>
            <h4 className="t-h2">Bundle detail</h4>
          </div>
          <p className="cap" style={{ marginBottom: 8 }}>ID: <span className="faint">{selectedBundle.id}</span></p>
          <div className="bundles-metadata-form" style={{ marginBottom: 12 }}>
            <div className="t-h2" style={{ marginBottom: 8 }}>Bundle metadata</div>
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

function UploadValidatePanel({ modulesUrl, onBack, onCreatePlan }) {
  const [bundleFile, setBundleFile] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState(null);
  const [activeHelpCheck, setActiveHelpCheck] = useState(null);
  const [copied, setCopied] = useState(false);

  const onSubmit = async (event) => {
    event.preventDefault();
    if (!bundleFile) {
      setSubmitError("Please choose a .zip bundle file.");
      return;
    }
    setIsSubmitting(true);
    setSubmitError("");
    setResult(null);
    try {
      const importResp = await fetch(`${modulesUrl}/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "upload", source_ref: bundleFile.name }),
      });
      if (!importResp.ok) {
        throw new Error(await parseError(importResp, `Import failed (${importResp.status})`));
      }
      const imported = await importResp.json();

      const formData = new FormData();
      formData.append("bundle", bundleFile);
      const validateResp = await fetch(`${modulesUrl}/${imported.id}/validate-upload`, {
        method: "POST",
        body: formData,
      });
      if (!validateResp.ok) {
        throw new Error(await parseError(validateResp, `Validation failed (${validateResp.status})`));
      }
      const validated = await validateResp.json();
      setResult({ moduleId: imported.id, ...validated });
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="panel card-pad bundles-section">
      <div className="row between" style={{ marginBottom: 10 }}>
        <h2 className="t-h2">Step 2: Upload and validate bundle</h2>
        <Button variant="ghost" size="sm" onClick={onBack}>Back</Button>
      </div>
      <p className="cap" style={{ marginBottom: 14 }}>Upload a .zip bundle and run validation directly from archive contents.</p>

      <form className="bundles-form col gap-2" onSubmit={onSubmit}>
        <label className="bundles-label" htmlFor="bundle-file">Bundle zip</label>
        <input
          id="bundle-file"
          className="bundles-file-input"
          type="file"
          accept=".zip,application/zip"
          onChange={(event) => setBundleFile(event.target.files?.[0] || null)}
          required
        />

        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn btn-primary" type="submit" disabled={isSubmitting || !bundleFile}>{isSubmitting ? "Validating..." : "Upload + validate"}</button>
        </div>
      </form>

      {isSubmitting ? <LoadingState label="Running validation..." /> : null}
      {submitError ? <ErrorState title="Validation failed" description={submitError} /> : null}

      {result ? (
        <div className="panel card-pad bundles-validation-result">
          <div className="row between" style={{ marginBottom: 10 }}>
            <h3 className="t-h2">Validation result</h3>
            <span className="t-label">{result.validation_status}</span>
          </div>
          <p className="cap" style={{ marginBottom: 10 }}>Module ID: <span className="faint">{result.moduleId}</span></p>
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
                  <span className="t-label">{diag.level || "info"}</span> {diag.code}: {diag.message}
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
