import { useEffect, useMemo, useState } from "react";
import { createDashboardDataAdapter } from "./data/dashboardDataAdapter";
import { mockDashboardProvider } from "./data/mockDashboardProvider";

const defaultAdapter = createDashboardDataAdapter(mockDashboardProvider);

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
