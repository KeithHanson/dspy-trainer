export function normalizeApiBaseUrl(configuredBase = import.meta.env.VITE_API_BASE_URL) {
  const trimmedBase = typeof configuredBase === "string" ? configuredBase.trim() : "";
  return (trimmedBase || "/api").replace(/\/$/, "");
}

export function buildApiUrl(path, configuredBase = import.meta.env.VITE_API_BASE_URL) {
  return `${normalizeApiBaseUrl(configuredBase)}${path}`;
}
