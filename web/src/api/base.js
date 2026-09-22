function normalizeBaseUrl(configuredBase, fallbackPath) {
  const trimmedBase = typeof configuredBase === "string" ? configuredBase.trim() : "";
  return (trimmedBase || fallbackPath).replace(/\/$/, "");
}

function toAbsoluteBrowserUrl(baseUrl) {
  if (/^https?:\/\//i.test(baseUrl)) {
    return baseUrl;
  }
  if (typeof window !== "undefined" && window.location?.origin) {
    return `${window.location.origin}${baseUrl.startsWith("/") ? "" : "/"}${baseUrl}`;
  }
  return baseUrl;
}

export function normalizeApiBaseUrl(configuredBase = import.meta.env.VITE_API_BASE_URL) {
  return normalizeBaseUrl(configuredBase, "/api");
}

export function normalizeMlflowBaseUrl(configuredBase = import.meta.env.VITE_MLFLOW_BASE_URL) {
  return normalizeBaseUrl(configuredBase, "/mlflow");
}

export function buildApiUrl(path, configuredBase = import.meta.env.VITE_API_BASE_URL) {
  return `${normalizeApiBaseUrl(configuredBase)}${path}`;
}

export function buildAbsoluteApiUrl(path, configuredBase = import.meta.env.VITE_API_BASE_URL) {
  return `${toAbsoluteBrowserUrl(normalizeApiBaseUrl(configuredBase))}${path}`;
}

export function buildMlflowUrl(path = "", configuredBase = import.meta.env.VITE_MLFLOW_BASE_URL) {
  return `${normalizeMlflowBaseUrl(configuredBase)}${path}`;
}
