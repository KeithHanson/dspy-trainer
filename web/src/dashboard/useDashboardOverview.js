import { useEffect, useMemo, useState } from "react";
import { createDashboardDataAdapter } from "./data/dashboardDataAdapter";
import { createLiveDashboardProvider } from "./data/liveDashboardProvider";

const defaultAdapter = createDashboardDataAdapter(
  createLiveDashboardProvider(import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"),
);

export function useDashboardOverview(adapter = defaultAdapter) {
  const stableAdapter = useMemo(() => adapter, [adapter]);
  const [state, setState] = useState({ isLoading: true, error: null, data: null });

  useEffect(() => {
    let cancelled = false;

    async function loadOverview() {
      setState({ isLoading: true, error: null, data: null });
      try {
        const overview = await stableAdapter.getOverview();
        if (!cancelled) {
          setState({ isLoading: false, error: null, data: overview });
        }
      } catch (error) {
        if (!cancelled) {
          setState({ isLoading: false, error, data: null });
        }
      }
    }

    loadOverview();

    return () => {
      cancelled = true;
    };
  }, [stableAdapter]);

  return state;
}
