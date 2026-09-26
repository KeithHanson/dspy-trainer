import { useEffect, useMemo, useState } from "react";
import { buildApiUrl } from "../api/base";
import { Button } from "../components/primitives/Button";
import { ErrorState } from "../components/states/ErrorState";
import { LoadingState } from "../components/states/LoadingState";

const ACTIVE_BUILD_STATUSES = new Set(["queued", "building"]);
const MAX_VISIBLE_BUILD_LOG_CHARS = 16_384;

function sanitizeBuildOutput(value) {
  return String(value || "")
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]")
    .replace(/((?:password|secret|token|api[_-]?key|authorization|credential)[A-Za-z0-9_.-]*\s*[:=]\s*)[^\r\n]*/gi, "$1[REDACTED]")
    .slice(0, MAX_VISIBLE_BUILD_LOG_CHARS);
}

function formatDateTime(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "unknown" : parsed.toLocaleString();
}

async function parseError(response, fallback) {
  try {
    const payload = await response.json();
    return payload?.error ? `${fallback}: ${sanitizeBuildOutput(payload.error)}` : fallback;
  } catch {
    return fallback;
  }
}

function BuildStatusPill({ status }) {
  const normalized = String(status || "unknown").toLowerCase();
  const tone = normalized === "ready"
    ? "runs-status-pill-pass"
    : normalized === "failed"
      ? "runs-status-pill-fail"
      : ACTIVE_BUILD_STATUSES.has(normalized)
        ? "runs-status-pill-run"
        : "runs-status-pill-neutral";
  return <span className={`plans-status ${tone}`}>{normalized}</span>;
}

export function RevisionImageBuildsPanel({ active, moduleId }) {
  const buildsUrl = useMemo(() => buildApiUrl("/revision-image-builds"), []);
  const [builds, setBuilds] = useState([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [actionBuildId, setActionBuildId] = useState("");
  const [isRebuildingAll, setIsRebuildingAll] = useState(false);
  const [error, setError] = useState("");
  const [selectedBuildId, setSelectedBuildId] = useState("");
  const [buildLog, setBuildLog] = useState(null);
  const [logError, setLogError] = useState("");

  const requestBuilds = async (signal) => {
    const response = await fetch(`${buildsUrl}?module_id=${encodeURIComponent(moduleId)}&limit=100&offset=0`, {
      method: "GET",
      signal,
    });
    if (!response.ok) {
      throw new Error(await parseError(response, `Could not load image builds (${response.status})`));
    }
    const payload = await response.json();
    return Array.isArray(payload?.items) ? payload.items : [];
  };

  const refreshBuilds = async () => {
    const items = await requestBuilds();
    setBuilds(items);
    setHasLoaded(true);
  };

  useEffect(() => {
    if (!active || hasLoaded) return undefined;
    const controller = new AbortController();
    setIsLoading(true);
    setError("");
    requestBuilds(controller.signal)
      .then((items) => {
        setBuilds(items);
        setHasLoaded(true);
      })
      .catch((loadError) => {
        if (loadError?.name !== "AbortError") {
          setError(loadError instanceof Error ? loadError.message : "Could not load image builds");
        }
      })
      .finally(() => setIsLoading(false));
    return () => controller.abort();
  }, [active, hasLoaded, moduleId, buildsUrl]);

  const hasNonterminalBuild = builds.some((build) => ACTIVE_BUILD_STATUSES.has(String(build?.status || "")));
  useEffect(() => {
    if (!hasNonterminalBuild) return undefined;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      try {
        const items = await requestBuilds();
        if (!cancelled) {
          setBuilds(items);
          setError("");
        }
      } catch (pollError) {
        if (!cancelled) {
          setError(pollError instanceof Error ? pollError.message : "Could not refresh image builds");
        }
      }
      if (!cancelled) timer = window.setTimeout(poll, 2_000);
    };
    timer = window.setTimeout(poll, 2_000);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [hasNonterminalBuild, moduleId, buildsUrl]);

  const selectedBuild = builds.find((build) => build.id === selectedBuildId) || null;
  useEffect(() => {
    if (!selectedBuildId) {
      setBuildLog(null);
      setLogError("");
      return undefined;
    }
    const controller = new AbortController();
    const loadLog = async () => {
      setLogError("");
      try {
        const response = await fetch(`${buildsUrl}/${encodeURIComponent(selectedBuildId)}/logs?offset=0&limit=16384`, {
          method: "GET",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(await parseError(response, `Could not load build log (${response.status})`));
        }
        const payload = await response.json();
        setBuildLog({
          text: sanitizeBuildOutput(payload?.text),
          totalBytes: Number(payload?.total_bytes || 0),
          truncated: payload?.next_offset !== null && payload?.next_offset !== undefined,
        });
      } catch (loadError) {
        if (loadError?.name !== "AbortError") {
          setLogError(loadError instanceof Error ? loadError.message : "Could not load build log");
        }
      }
    };
    loadLog();
    return () => controller.abort();
  }, [selectedBuildId, selectedBuild?.updated_at, buildsUrl]);

  const retryBuild = async (build) => {
    if (!window.confirm(`Retry failed image build ${build.id}?`)) return;
    setActionBuildId(build.id);
    setError("");
    try {
      const response = await fetch(`${buildsUrl}/${encodeURIComponent(build.id)}/retry`, { method: "POST" });
      if (!response.ok) {
        throw new Error(await parseError(response, `Could not retry image build (${response.status})`));
      }
      await refreshBuilds();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Could not retry image build");
    } finally {
      setActionBuildId("");
    }
  };

  const rebuildAll = async () => {
    if (!window.confirm("Rebuild the current revision of every module? This creates new immutable build generations.")) return;
    setIsRebuildingAll(true);
    setError("");
    try {
      const response = await fetch(`${buildsUrl}/rebuild-all`, { method: "POST" });
      if (!response.ok) {
        throw new Error(await parseError(response, `Could not rebuild current revisions (${response.status})`));
      }
      await refreshBuilds();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Could not rebuild current revisions");
    } finally {
      setIsRebuildingAll(false);
    }
  };

  if (!active) return null;
  const actionInFlight = Boolean(actionBuildId) || isRebuildingAll;
  return (
    <section className="panel card-pad bundles-section" aria-label="Image generation status">
      <div className="row between image-builds-head">
        <div>
          <h2 className="t-h2">Image generation</h2>
          <p className="cap">Source sync and validation finish independently. Each valid revision receives an immutable image build.</p>
        </div>
        <Button size="sm" variant="primary" onClick={rebuildAll} disabled={actionInFlight}>
          {isRebuildingAll ? "Queuing rebuilds..." : "Rebuild all current revisions"}
        </Button>
      </div>
      {error ? <ErrorState title="Image build action failed" description={error} /> : null}
      {isLoading ? <LoadingState label="Loading image builds..." /> : null}
      {!isLoading && hasLoaded && !builds.length ? <p className="cap">No image builds recorded for this module yet.</p> : null}
      <div className="image-builds-list">
        {builds.map((build) => (
          <article key={build.id} className="image-build-card">
            <div className="row between image-build-card-head">
              <div className="col gap-1">
                <strong>Generation {build.generation}</strong>
                <span className="mono cap">Build {build.id}</span>
              </div>
              <BuildStatusPill status={build.status} />
            </div>
            <dl className="image-build-meta">
              <div><dt>Revision</dt><dd className="mono">{build.revision_id || "unknown"}</dd></div>
              <div><dt>Source commit</dt><dd className="mono">{build.source_commit || "unknown"}</dd></div>
              <div><dt>Attempt</dt><dd>{build.attempt ?? 0}</dd></div>
              <div><dt>Queued</dt><dd>{build.queued_at ? formatDateTime(build.queued_at) : "unknown"}</dd></div>
              <div><dt>Image ID</dt><dd className="mono">{build.image_id || build.image_digest || "Not produced"}</dd></div>
              <div><dt>Base image</dt><dd className="mono">{build.base_image_id || "unknown"}</dd></div>
            </dl>
            {build.failure_reason ? (
              <div className="image-build-failure" role="alert">
                <strong>Failure reason</strong>
                <span>{sanitizeBuildOutput(build.failure_reason)}</span>
              </div>
            ) : null}
            <div className="row gap-2 image-build-actions">
              <Button size="sm" onClick={() => setSelectedBuildId((current) => current === build.id ? "" : build.id)}>
                {selectedBuildId === build.id ? "Hide log" : "View bounded log"}
              </Button>
              {build.status === "failed" ? (
                <Button size="sm" variant="primary" onClick={() => retryBuild(build)} disabled={actionInFlight}>
                  {actionBuildId === build.id ? "Retrying..." : "Retry failed build"}
                </Button>
              ) : null}
            </div>
            {selectedBuildId === build.id ? (
              <div className="image-build-log-wrap">
                {logError ? <ErrorState title="Build log unavailable" description={logError} /> : null}
                {buildLog ? (
                  <>
                    <p className="cap">Showing the first bounded log segment ({buildLog.totalBytes} retained bytes){buildLog.truncated ? "; additional output is intentionally hidden." : "."}</p>
                    <pre className="image-build-log" aria-label={`Build log for ${build.id}`}>{buildLog.text || "No build output recorded."}</pre>
                  </>
                ) : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
