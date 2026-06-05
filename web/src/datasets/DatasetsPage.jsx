import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { Icon } from "../components/Icon";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const PROJECT_ID = "proj-1";

function apiBaseUrl() {
  return (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

function getBundleDisplayName(bundle) {
  return bundle?.bundle_name || bundle?.id || "Untitled bundle";
}

function normalizeContractFields(fields) {
  if (!Array.isArray(fields)) {
    return [];
  }
  return fields
    .filter((field) => field && typeof field.key === "string" && field.key.trim())
    .map((field) => ({
      key: field.key.trim(),
      label: typeof field.label === "string" && field.label.trim() ? field.label.trim() : field.key.trim(),
      description: typeof field.description === "string" && field.description.trim() ? field.description.trim() : "",
      required: field.required !== false,
    }));
}

function getEvaluationContract(bundle) {
  const contract = bundle?.evaluation_contract;
  return {
    inputFields: normalizeContractFields(contract?.input_fields),
    labelFields: normalizeContractFields(contract?.label_fields),
    inputTemplate: contract?.input_template && typeof contract.input_template === "object" && !Array.isArray(contract.input_template) ? contract.input_template : null,
    labelTemplate: contract?.label_template && typeof contract.label_template === "object" && !Array.isArray(contract.label_template) ? contract.label_template : null,
  };
}

function stringifyJson(value) {
  return JSON.stringify(value, null, 2);
}

function parseApiErrorMessage(body) {
  return typeof body?.error === "string" && body.error ? body.error : "";
}

function buildTemplatePayload(fields, fallbackTemplate) {
  if (fallbackTemplate && typeof fallbackTemplate === "object" && !Array.isArray(fallbackTemplate) && Object.keys(fallbackTemplate).length) {
    return fallbackTemplate;
  }
  if (!fields.length) {
    return {};
  }
  return Object.fromEntries(fields.map((field) => [field.key, ""]));
}

function createItemFromPayloads(id, inputPayload, labelPayload) {
  return {
    id,
    inputText: stringifyJson(inputPayload),
    labelText: stringifyJson(labelPayload),
  };
}

function createEmptyItem(contract, id) {
  return createItemFromPayloads(
    id,
    buildTemplatePayload(contract?.inputFields || [], contract?.inputTemplate),
    buildTemplatePayload(contract?.labelFields || [], contract?.labelTemplate),
  );
}

function parseJsonObject(text, sideLabel) {
  const trimmed = text.trim();
  if (!trimmed) {
    return { value: {}, error: `${sideLabel} is required.` };
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { value: {}, error: `${sideLabel} must be a JSON object.` };
    }
    return { value: parsed, error: "" };
  } catch {
    return { value: {}, error: `${sideLabel} must be valid JSON.` };
  }
}

function hasMeaningfulValue(value) {
  if (typeof value === "string") {
    return value.trim().length > 0;
  }
  if (Array.isArray(value)) {
    return value.some((item) => hasMeaningfulValue(item));
  }
  if (value && typeof value === "object") {
    return Object.values(value).some((item) => hasMeaningfulValue(item));
  }
  return value !== null && value !== undefined;
}

function validatePayloadAgainstContract(payload, fields, sideLabel) {
  if (!fields.length) {
    return Object.keys(payload).length ? "" : `${sideLabel} must not be empty.`;
  }
  const missing = fields
    .filter((field) => field.required)
    .filter((field) => !Object.prototype.hasOwnProperty.call(payload, field.key) || !hasMeaningfulValue(payload[field.key]));
  if (!missing.length) {
    return "";
  }
  return `${sideLabel} is missing required field${missing.length === 1 ? "" : "s"}: ${missing.map((field) => field.key).join(", ")}.`;
}

function analyzeItem(item, contract) {
  const inputParsed = parseJsonObject(item.inputText, "Input JSON");
  const labelParsed = parseJsonObject(item.labelText, "Label JSON");
  const errors = [];
  if (inputParsed.error) {
    errors.push(inputParsed.error);
  }
  if (labelParsed.error) {
    errors.push(labelParsed.error);
  }
  if (!inputParsed.error) {
    const contractError = validatePayloadAgainstContract(inputParsed.value, contract.inputFields, "Input JSON");
    if (contractError) {
      errors.push(contractError);
    }
  }
  if (!labelParsed.error) {
    const contractError = validatePayloadAgainstContract(labelParsed.value, contract.labelFields, "Label JSON");
    if (contractError) {
      errors.push(contractError);
    }
  }
  return {
    valid: errors.length === 0,
    errors,
    input: inputParsed.value,
    label: labelParsed.value,
  };
}

function summarizeItem(item, index) {
  const parsed = parseJsonObject(item.inputText, "Input JSON");
  const inputPayload = parsed.value;
  const inputKeys = Object.keys(inputPayload);
  let title = `Input ${index + 1}`;
  for (const key of inputKeys) {
    const value = inputPayload[key];
    if (typeof value === "string" && value.trim()) {
      title = value.trim();
      break;
    }
  }
  if (title.length > 54) {
    title = `${title.slice(0, 51)}...`;
  }
  const labelPayload = parseJsonObject(item.labelText, "Label JSON").value;
  const labelKeys = Object.keys(labelPayload);
  const inputSummary = inputKeys.slice(0, 3).join(", ") || "input";
  const labelSummary = labelKeys.slice(0, 3).join(", ") || "label";
  return { title, detail: `${inputSummary} -> ${labelSummary}` };
}

function formatDate(value) {
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

async function readApiError(response) {
  try {
    const body = await response.json();
    return parseApiErrorMessage(body);
  } catch {
    return "";
  }
}

export function DatasetsPage() {
  const navigate = useNavigate();
  const apiBase = useMemo(() => apiBaseUrl(), []);
  const [datasets, setDatasets] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [duplicatingId, setDuplicatingId] = useState("");

  const loadDatasets = async () => {
    setIsLoading(true);
    setError("");
    try {
      const response = await fetch(`${apiBase}/evaluation-datasets`, { method: "GET" });
      if (!response.ok) {
        throw new Error(`Could not load datasets (${response.status})`);
      }
      const payload = await response.json();
      setDatasets(Array.isArray(payload) ? payload : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load datasets");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDatasets();
  }, []);

  const deleteDataset = async (datasetId) => {
    setDeletingId(datasetId);
    setError("");
    try {
      const response = await fetch(`${apiBase}/evaluation-datasets/${datasetId}`, { method: "DELETE" });
      if (!response.ok) {
        const detail = await readApiError(response);
        throw new Error(detail || `Could not delete dataset (${response.status})`);
      }
      setDatasets((prev) => prev.filter((item) => item.id !== datasetId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete dataset");
    } finally {
      setDeletingId("");
    }
  };

  const duplicateDataset = async (dataset) => {
    setDuplicatingId(dataset.id);
    setError("");
    try {
      const response = await fetch(`${apiBase}/evaluation-datasets/${dataset.id}/duplicate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `${dataset.name || "Untitled dataset"} copy` }),
      });
      if (!response.ok) {
        const detail = await readApiError(response);
        throw new Error(detail || `Could not duplicate dataset (${response.status})`);
      }
      await loadDatasets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not duplicate dataset");
    } finally {
      setDuplicatingId("");
    }
  };

  return (
    <section className="page">
      <div className="page-body datasets-wrap">
        <header className="row between datasets-head">
          <div className="col gap-1">
            <h1 className="t-display" style={{ fontSize: 22 }}>Datasets</h1>
            <p className="muted t-sm">Bundle-scoped evaluation records that plans can reuse without embedding row data.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={loadDatasets} disabled={isLoading}>{isLoading ? "Refreshing..." : "Refresh"}</Button>
            <Button variant="primary" onClick={() => navigate("/datasets/new")}>New dataset</Button>
          </div>
        </header>

        {error ? <ErrorState title="Dataset error" description={error} /> : null}
        {isLoading ? <LoadingState label="Loading datasets..." /> : null}
        {!isLoading && !error ? (
          datasets.length ? (
            <div className="col gap-2">
              {datasets.map((dataset) => (
                <article key={dataset.id} className="bundles-saved-row datasets-row">
                  <div className="bundles-saved-icon center datasets-row-icon">
                    <Icon name="layers" size={18} />
                  </div>
                  <div className="bundles-row-btn datasets-row-meta">
                    <div className="row between datasets-row-top">
                      <strong>{dataset.name || "Untitled dataset"}</strong>
                      <div className="row gap-2">
                        <Button size="sm" onClick={() => navigate(`/datasets/${encodeURIComponent(dataset.id)}/edit`)}>Edit</Button>
                        <Button size="sm" onClick={() => duplicateDataset(dataset)} disabled={duplicatingId === dataset.id}>{duplicatingId === dataset.id ? "Duplicating..." : "Duplicate"}</Button>
                        <Button size="sm" variant="danger" onClick={() => deleteDataset(dataset.id)} disabled={deletingId === dataset.id}>{deletingId === dataset.id ? "Deleting..." : "Delete"}</Button>
                      </div>
                    </div>
                    <span className="cap mono">Bundle: {dataset.bundle_name || dataset.module_import_id}</span>
                    <span className="cap mono">{dataset.record_count || 0} items</span>
                    <span className="cap mono">Keys: {(dataset.input_keys || []).slice(0, 3).join(", ") || "input"} {"->"} {(dataset.label_keys || []).slice(0, 3).join(", ") || "expected"}</span>
                    <span className="cap mono">Updated {formatDate(dataset.updated_at)}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No datasets yet" description="Create a dataset to author reusable evaluation inputs and labels." />
          )
        ) : null}
      </div>
    </section>
  );
}

export function DatasetEditorPage() {
  const apiBase = useMemo(() => apiBaseUrl(), []);
  const navigate = useNavigate();
  const { datasetId } = useParams();
  const isEditing = Boolean(datasetId);
  const [modules, setModules] = useState([]);
  const [lmProfiles, setLmProfiles] = useState([]);
  const [isLoadingModules, setIsLoadingModules] = useState(false);
  const [isLoadingDataset, setIsLoadingDataset] = useState(false);
  const [error, setError] = useState("");
  const [validationError, setValidationError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedBundleId, setSelectedBundleId] = useState("");
  const [activeTab, setActiveTab] = useState("details");
  const [items, setItems] = useState([createEmptyItem(null, `item-${Date.now()}`)]);
  const [selectedItemId, setSelectedItemId] = useState("");
  const [isGeneratorOpen, setIsGeneratorOpen] = useState(false);
  const [generationLmProfileId, setGenerationLmProfileId] = useState("");
  const [generatorPrompt, setGeneratorPrompt] = useState("");
  const [generatorExamples, setGeneratorExamples] = useState("");
  const [isGeneratingRows, setIsGeneratingRows] = useState(false);
  const [generationError, setGenerationError] = useState("");

  const validModules = useMemo(() => modules.filter((item) => item.validation_status === "passed"), [modules]);
  const selectedBundle = useMemo(() => validModules.find((item) => item.id === selectedBundleId) || null, [selectedBundleId, validModules]);
  const evaluationContract = useMemo(() => getEvaluationContract(selectedBundle), [selectedBundle]);
  const inputPlaceholder = useMemo(() => stringifyJson(buildTemplatePayload(evaluationContract.inputFields, evaluationContract.inputTemplate)), [evaluationContract]);
  const labelPlaceholder = useMemo(() => stringifyJson(buildTemplatePayload(evaluationContract.labelFields, evaluationContract.labelTemplate)), [evaluationContract]);
  const analyzedItems = useMemo(() => items.map((item) => ({ item, ...analyzeItem(item, evaluationContract) })), [items, evaluationContract]);
  const selectedAnalysis = useMemo(() => analyzedItems.find((entry) => entry.item.id === selectedItemId) || analyzedItems[0] || null, [analyzedItems, selectedItemId]);

  useEffect(() => {
    const loadModules = async () => {
      setIsLoadingModules(true);
      try {
        const response = await fetch(`${apiBase}/modules`, { method: "GET" });
        if (!response.ok) {
          throw new Error(`Could not load bundles (${response.status})`);
        }
        const payload = await response.json();
        const nextModules = Array.isArray(payload) ? payload : [];
        setModules(nextModules);
        if (!selectedBundleId) {
          const firstValid = nextModules.find((item) => item.validation_status === "passed");
          if (firstValid) {
            setSelectedBundleId(firstValid.id);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load bundles");
      } finally {
        setIsLoadingModules(false);
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
    loadLmProfiles();
  }, [apiBase]);

  useEffect(() => {
    const loadDataset = async () => {
      if (!datasetId) {
        return;
      }
      setIsLoadingDataset(true);
      try {
        const response = await fetch(`${apiBase}/evaluation-datasets/${datasetId}`, { method: "GET" });
        if (!response.ok) {
          throw new Error(`Could not load dataset (${response.status})`);
        }
        const payload = await response.json();
        setName(payload.name || "");
        setDescription(payload.description || "");
        setSelectedBundleId(payload.module_import_id || "");
        const nextItems = Array.isArray(payload.records) && payload.records.length
          ? payload.records.map((record, index) => createItemFromPayloads(record.id || `loaded-${index}`, record.input || {}, record.label || {}))
          : [createEmptyItem(null, `item-${Date.now()}`)];
        setItems(nextItems);
        setSelectedItemId(nextItems[0]?.id || "");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load dataset");
      } finally {
        setIsLoadingDataset(false);
      }
    };
    loadDataset();
  }, [apiBase, datasetId]);

  useEffect(() => {
    if (!selectedItemId && items.length) {
      setSelectedItemId(items[0].id);
    }
  }, [items, selectedItemId]);

  useEffect(() => {
    if (!generationLmProfileId && lmProfiles.length) {
      setGenerationLmProfileId(lmProfiles[0].id || "");
    }
  }, [generationLmProfileId, lmProfiles]);

  useEffect(() => {
    setItems((prev) => {
      if (!prev.length) {
        return [createEmptyItem(evaluationContract, `item-${Date.now()}`)];
      }
      return prev;
    });
  }, [evaluationContract]);

  const updateItemField = (itemId, field, value) => {
    setItems((prev) => prev.map((item) => (item.id === itemId ? { ...item, [field]: value } : item)));
  };

  const addItem = () => {
    const nextItem = createEmptyItem(evaluationContract, `item-${Date.now()}`);
    setItems((prev) => [...prev, nextItem]);
    setSelectedItemId(nextItem.id);
    setActiveTab("items");
  };

  const duplicateSelectedItem = () => {
    if (!selectedAnalysis) {
      return;
    }
    const duplicate = {
      ...selectedAnalysis.item,
      id: `item-${Date.now()}`,
    };
    setItems((prev) => {
      const index = prev.findIndex((item) => item.id === selectedAnalysis.item.id);
      if (index === -1) {
        return [...prev, duplicate];
      }
      return [...prev.slice(0, index + 1), duplicate, ...prev.slice(index + 1)];
    });
    setSelectedItemId(duplicate.id);
    setActiveTab("items");
  };

  const deleteSelectedItem = () => {
    if (!selectedAnalysis || items.length <= 1) {
      return;
    }
    setItems((prev) => {
      const index = prev.findIndex((item) => item.id === selectedAnalysis.item.id);
      const next = prev.filter((item) => item.id !== selectedAnalysis.item.id);
      const fallback = next[Math.max(0, index - 1)] || next[0] || null;
      setSelectedItemId(fallback?.id || "");
      return next;
    });
  };

  const insertGeneratedRows = (rows) => {
    if (!rows.length) {
      return;
    }
    const insertedAt = Date.now();
    const nextItems = rows.map((row, index) => createItemFromPayloads(`generated-${insertedAt}-${index}`, row.input, row.label));
    setItems((prev) => [...prev, ...nextItems]);
    setSelectedItemId(nextItems[0]?.id || selectedItemId);
    setIsGeneratorOpen(false);
    setGenerationError("");
  };

  const generateRowsPreview = async () => {
    setGenerationError("");
    if (!selectedBundleId) {
      setGenerationError("Select a validated bundle before generating items.");
      return;
    }
    if (!generationLmProfileId) {
      setGenerationError("Select an LM profile before generating items.");
      return;
    }
    if (!generatorPrompt.trim()) {
      setGenerationError("Describe the dataset items you want before generating.");
      return;
    }

    const promptSections = [generatorPrompt.trim()];
    if (generatorExamples.trim()) {
      promptSections.push(`Reference examples and constraints:\n${generatorExamples.trim()}`);
    }

    setIsGeneratingRows(true);
    try {
      const response = await fetch(`${apiBase}/evaluation-datasets/generate-rows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lm_profile_id: generationLmProfileId,
          module_import_id: selectedBundleId,
          operator_prompt: promptSections.join("\n\n"),
          existing_rows: analyzedItems.filter((entry) => entry.valid).map((entry) => ({ input: entry.input, label: entry.label })),
          max_rows: 5,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(parseApiErrorMessage(body) || `Could not generate items (${response.status})`);
      }
      const payload = await response.json();
      const previewRows = Array.isArray(payload?.items)
        ? payload.items.filter((item) => item && typeof item === "object" && !Array.isArray(item))
            .map((item, index) => ({
              id: `preview-${index}`,
              input: item?.input && typeof item.input === "object" && !Array.isArray(item.input) ? item.input : {},
              label: item?.label && typeof item.label === "object" && !Array.isArray(item.label) ? item.label : {},
            }))
            .filter((item) => Object.keys(item.input).length && Object.keys(item.label).length)
        : [];
      if (!previewRows.length) {
        throw new Error("Generated items could not be previewed.");
      }
      insertGeneratedRows(previewRows);
    } catch (err) {
      setGenerationError(err instanceof Error ? err.message : "Could not generate items");
    } finally {
      setIsGeneratingRows(false);
    }
  };

  const saveDataset = async () => {
    setValidationError("");
    setError("");
    if (!name.trim()) {
      setValidationError("Dataset name is required.");
      return;
    }
    if (!selectedBundleId) {
      setValidationError("Select a validated bundle.");
      return;
    }
    if (!selectedAnalysis) {
      setValidationError("Add at least one dataset item.");
      return;
    }
    const invalid = analyzedItems.filter((entry) => !entry.valid);
    if (invalid.length) {
      setValidationError(`Fix ${invalid.length} invalid dataset item${invalid.length === 1 ? "" : "s"} before saving.`);
      return;
    }

    setIsSaving(true);
    try {
      const payload = {
        project_id: PROJECT_ID,
        name: name.trim(),
        description: description.trim() || null,
        module_import_id: selectedBundleId,
        records: analyzedItems.map((entry) => ({ id: entry.item.id, input: entry.input, label: entry.label })),
      };
      const url = isEditing ? `${apiBase}/evaluation-datasets/${datasetId}` : `${apiBase}/evaluation-datasets`;
      const method = isEditing ? "PATCH" : "POST";
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const detail = await readApiError(response);
        throw new Error(detail || `Could not save dataset (${response.status})`);
      }
      navigate("/datasets");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save dataset");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="page">
      <header className="page-head row between plans-builder-head">
        <div className="col gap-1">
          <h1 className="t-h1">{isEditing ? "Edit dataset" : "New dataset"}</h1>
          <p className="cap">Author reusable input and label records for a selected bundle.</p>
        </div>
        <div className="row gap-2">
          <Link className="lnk" to="/datasets">Back to datasets</Link>
          <Button onClick={saveDataset} disabled={isSaving}>{isSaving ? "Saving..." : "Save dataset"}</Button>
        </div>
      </header>

      <div className={`page-body datasets-editor-page ${activeTab === "items" ? "datasets-editor-page-items" : ""}`}>
        {error ? <ErrorState title="Dataset error" description={error} /> : null}
        {validationError ? <ErrorState title="Validation required" description={validationError} /> : null}
        {isLoadingModules ? <LoadingState label="Loading bundles..." /> : null}
        {isLoadingDataset ? <LoadingState label="Loading dataset..." /> : null}

        <div className="row gap-2 datasets-tabs" role="tablist" aria-label="Dataset editor tabs">
          <button type="button" role="tab" aria-selected={activeTab === "details"} className={`datasets-tab ${activeTab === "details" ? "datasets-tab-active" : ""}`} onClick={() => setActiveTab("details")}>Details</button>
          <button type="button" role="tab" aria-selected={activeTab === "items"} className={`datasets-tab ${activeTab === "items" ? "datasets-tab-active" : ""}`} onClick={() => setActiveTab("items")}>Items</button>
        </div>

        {activeTab === "details" ? (
          <div className="datasets-details-panel">
            <div className="datasets-editor-main datasets-editor-main-full">
              <section className="panel card-pad plans-form-block">
                <label className="t-label plans-input-label" htmlFor="dataset-name">Dataset name</label>
                <input id="dataset-name" className="bundles-input" value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Support triage regression set" />
              </section>

              <section className="panel card-pad plans-form-block">
                <label className="t-label plans-input-label" htmlFor="dataset-description">Description</label>
                <textarea id="dataset-description" className="plans-textarea" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Optional notes about what this dataset covers." />
              </section>

              <section className="panel card-pad plans-form-block">
                <div className="row between" style={{ marginBottom: 10 }}>
                  <h2 className="t-h2">Bundle scope</h2>
                  <span className="t-label">Validated only</span>
                </div>
                {!validModules.length ? (
                  <EmptyState title="No validated bundles" description="Validate a bundle before creating a dataset." />
                ) : (
                  <div className="col gap-2">
                    {validModules.map((bundle) => (
                      <button key={bundle.id} className={`plans-bundle-option ${selectedBundleId === bundle.id ? "plans-bundle-option-active" : ""}`} type="button" onClick={() => setSelectedBundleId(bundle.id)}>
                        <span className="t-sm" style={{ fontWeight: 600 }}>{getBundleDisplayName(bundle)}</span>
                        <span className="cap mono">{bundle.bundle_version ? `v${bundle.bundle_version}` : "version n/a"}</span>
                      </button>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </div>
        ) : null}

        {activeTab === "items" ? (
          <div className="datasets-items-layout">
            <aside className="datasets-items-rail datasets-tab-panel">
              <div className="row between datasets-items-rail-head">
                <div className="row gap-2"><span className="t-label">Items</span></div>
                <div className="row gap-2">
                  <Button size="sm" onClick={() => setIsGeneratorOpen(true)}>Generate with LLM</Button>
                  <Button size="sm" onClick={addItem}>Add item</Button>
                </div>
              </div>
              <div className="datasets-items-scroll col gap-2">
                {items.map((item, index) => {
                  const summary = summarizeItem(item, index);
                  const isActive = item.id === selectedItemId;
                  return (
                    <button key={item.id} type="button" className={`datasets-item-card ${isActive ? "datasets-item-card-active" : ""}`} onClick={() => setSelectedItemId(item.id)}>
                      <span className="datasets-item-index mono">Input {index + 1}</span>
                      <strong className="datasets-item-title">{summary.title}</strong>
                      <span className="cap mono datasets-item-detail">{summary.detail}</span>
                    </button>
                  );
                })}
              </div>
              <button type="button" className="datasets-add-card" onClick={addItem}>+ Add item</button>
            </aside>

            <section className="datasets-editor-panel datasets-tab-panel">
              {selectedAnalysis ? (
                <div className="col gap-3">
                  <div className="row between datasets-editor-item-head">
                    <div className="col gap-1">
                      <div className="t-h2">{summarizeItem(selectedAnalysis.item, items.findIndex((item) => item.id === selectedAnalysis.item.id)).title}</div>
                      <span className="cap mono">{selectedBundle ? getBundleDisplayName(selectedBundle) : "No bundle selected"}</span>
                    </div>
                    <div className="row gap-2">
                      <Button size="sm" onClick={duplicateSelectedItem}>Duplicate</Button>
                      <Button size="sm" variant="danger" onClick={deleteSelectedItem} disabled={items.length <= 1}>Delete</Button>
                    </div>
                  </div>

                  <div className="panel card-pad col gap-2 datasets-schema-card">
                    <div className="t-label">Bundle eval schema</div>
                    <span className="muted t-sm">Input: {evaluationContract.inputFields.map((field) => field.key).join(", ") || "any JSON object"}</span>
                    <span className="muted t-sm">Label: {evaluationContract.labelFields.map((field) => field.key).join(", ") || "any JSON object"}</span>
                  </div>

                  <div className="col gap-1">
                    <span className="t-label">Input JSON</span>
                    <textarea className="plans-textarea datasets-editor-textarea" rows={10} value={selectedAnalysis.item.inputText} onChange={(event) => updateItemField(selectedAnalysis.item.id, "inputText", event.target.value)} placeholder={inputPlaceholder} />
                  </div>

                  <div className="col gap-1">
                    <span className="t-label">Label JSON</span>
                    <textarea className="plans-textarea datasets-editor-textarea" rows={10} value={selectedAnalysis.item.labelText} onChange={(event) => updateItemField(selectedAnalysis.item.id, "labelText", event.target.value)} placeholder={labelPlaceholder} />
                  </div>

                  {selectedAnalysis.errors.length ? (
                    <div className="panel card-pad datasets-errors-panel">
                      <div className="t-label" style={{ marginBottom: 6 }}>Validation</div>
                      <div className="col gap-1">
                        {selectedAnalysis.errors.map((item) => <span key={item} className="muted t-sm">{item}</span>)}
                      </div>
                    </div>
                  ) : (
                    <div className="cap mono datasets-valid-copy">Valid JSON and required keys present.</div>
                  )}
                </div>
              ) : null}
            </section>
          </div>
        ) : null}
      </div>

      {isGeneratorOpen ? (
        <div className="bundles-modal-backdrop" onClick={() => setIsGeneratorOpen(false)}>
          <div className="panel card-pad bundles-modal plans-generate-modal" onClick={(event) => event.stopPropagation()}>
            <div className="row between" style={{ marginBottom: 10, alignItems: "flex-start" }}>
              <div>
                <h2 className="t-h2">Generate dataset items</h2>
                <p className="muted t-sm">Use an LM profile to draft input and label JSON that already matches the selected bundle schema.</p>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setIsGeneratorOpen(false)}>Close</Button>
            </div>

            <label className="col gap-1" style={{ marginBottom: 12 }} htmlFor="dataset-generator-lm-profile">
              <span className="t-label">LM profile</span>
              <select id="dataset-generator-lm-profile" className="bundles-input" value={generationLmProfileId} onChange={(event) => setGenerationLmProfileId(event.target.value)}>
                <option value="">Select an LM profile...</option>
                {lmProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name || profile.id}</option>
                ))}
              </select>
            </label>

            <label className="col gap-1" style={{ marginBottom: 12 }}>
              <span className="t-label">What items do you need?</span>
              <textarea className="plans-textarea" rows={5} value={generatorPrompt} onChange={(event) => setGeneratorPrompt(event.target.value)} placeholder="Describe the kinds of inputs and labels you want generated." />
            </label>

            <label className="col gap-1" style={{ marginBottom: 12 }}>
              <span className="t-label">Reference examples or constraints</span>
              <textarea className="plans-textarea" rows={6} value={generatorExamples} onChange={(event) => setGeneratorExamples(event.target.value)} placeholder="Optional: paste style notes, sample labels, or rubric guidance for the judge." />
            </label>

            <div className="row gap-2" style={{ marginBottom: 12 }}>
              <Button variant="primary" onClick={generateRowsPreview} disabled={isGeneratingRows}>{isGeneratingRows ? "Generating..." : "Generate items"}</Button>
            </div>

            {generationError ? <div className="plans-validation-alert" role="alert"><p className="plans-validation-copy">{generationError}</p></div> : null}

            <p className="muted t-sm">
              Generated items insert immediately and close this dialog. You can review or delete any row from the dataset editor afterward.
            </p>
          </div>
        </div>
      ) : null}
    </section>
  );
}
