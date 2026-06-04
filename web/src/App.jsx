import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { DashboardPage } from "./dashboard/DashboardPage";
import { BundlesPage } from "./bundles/BundlesPage";
import { PlansPage } from "./plans/PlansPage";
import { RunsPage } from "./runs/RunsPage";
import { LmProfileEditorPage, LmProfilesPage } from "./lmProfiles/LmProfilesPage";
import { OptimizationLaunchPage } from "./optimization/OptimizationLaunchPage";
import { OptimizationJobsPage } from "./optimization/OptimizationJobsPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/bundles" element={<BundlesPage />} />
        <Route path="/plans" element={<PlansPage />} />
        <Route path="/optimization/jobs" element={<OptimizationJobsPage />} />
        <Route path="/optimization" element={<OptimizationLaunchPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/lm-profiles" element={<LmProfilesPage />} />
        <Route path="/lm-profiles/new" element={<LmProfileEditorPage />} />
        <Route path="/lm-profiles/:profileId/edit" element={<LmProfileEditorPage />} />
      </Routes>
    </AppShell>
  );
}
