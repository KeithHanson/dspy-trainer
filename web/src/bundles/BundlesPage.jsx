import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/primitives/Button";
import { EmptyState } from "../components/states/EmptyState";
import { ErrorState } from "../components/states/ErrorState";

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
  "Set bundle.toml name/version/lm_target values.",
  "Zip the folder root, then upload for validation.",
];

export function BundlesPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const showUploadIntent = searchParams.get("upload") === "1";

  const sampleUrl = useMemo(() => buildApiUrl("/samples/module-bundle"), []);

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
            <h1 className="t-display">Prepare your first DSPy module bundle</h1>
            <p className="muted t-sm">Start with a working sample, adapt it to your signature, then move to upload and validation.</p>
          </div>
          <div className="row gap-2">
            <Button onClick={handleDownload} disabled={isDownloading}>
              {isDownloading ? "Downloading..." : "Download sample bundle"}
            </Button>
            <Button variant="primary" onClick={() => navigate("/bundles?upload=1")}>Upload bundle</Button>
          </div>
        </header>

        {downloadError ? <ErrorState title="Download failed" description={downloadError} /> : null}

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

        {showUploadIntent ? (
          <section className="bundles-section">
            <EmptyState
              title="Upload flow is next in sequence"
              description="You are on Step 1 today. Once sample prep is complete, proceed to Step 2: upload and validate your bundle."
            />
          </section>
        ) : null}
      </div>
    </section>
  );
}
