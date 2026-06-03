function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validateOverview(data) {
  assert(isObject(data), "Dashboard adapter must return an object");
  assert(typeof data.greetingName === "string", "overview.greetingName must be a string");
  assert(typeof data.summaryLine === "string", "overview.summaryLine must be a string");
  assert(Array.isArray(data.kpis), "overview.kpis must be an array");
  assert(Array.isArray(data.recentJobs), "overview.recentJobs must be an array");
  assert(Array.isArray(data.workerTable), "overview.workerTable must be an array");
  assert(Array.isArray(data.quickStart), "overview.quickStart must be an array");
}

export function createDashboardDataAdapter(provider) {
  assert(isObject(provider), "Dashboard provider is required");
  assert(typeof provider.getOverview === "function", "Dashboard provider must implement getOverview()");

  return {
    async getOverview() {
      const overview = await provider.getOverview();
      validateOverview(overview);
      return overview;
    },
  };
}
