import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth0 } from "@auth0/auth0-react";
import { AppShell } from "./layout/AppShell";
import { EmptyState } from "./components/states/EmptyState";
import { LoadingState } from "./components/states/LoadingState";
import { ErrorState } from "./components/states/ErrorState";
import { AuthScreen } from "./auth/AuthScreen";
import { DashboardPage } from "./dashboard/DashboardPage";
import { BundlesPage } from "./bundles/BundlesPage";

function PlaceholderPage({ title, description }) {
  return (
    <section className="page">
      <header className="page-head">
        <h1 className="t-h1">{title}</h1>
      </header>
      <div className="page-body">
        <EmptyState title={title} description={description} />
      </div>
    </section>
  );
}

export function App() {
  const { error, isAuthenticated, isLoading, loginWithRedirect, logout, user } = useAuth0();
  const orgName = user?.org_name || user?.company || "Default";

  if (isLoading) {
    return (
      <main className="auth-loading center">
        <LoadingState label="Checking authentication..." />
      </main>
    );
  }

  if (error) {
    return (
      <main className="auth-loading center">
        <ErrorState title="Authentication unavailable" description={error.message} />
      </main>
    );
  }

  if (!isAuthenticated) {
    return <AuthScreen loginWithRedirect={loginWithRedirect} orgName={orgName} />;
  }

  return (
    <AppShell
      onSignOut={() =>
        logout({
          logoutParams: {
            returnTo: window.location.origin,
          },
        })
      }
      user={user}
      orgName={orgName}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage user={user} />} />
        <Route path="/bundles" element={<BundlesPage />} />
        <Route path="/plans" element={<PlaceholderPage title="Evaluation Plans" description="Plan builder and run orchestration will be layered next." />} />
        <Route path="/runs" element={<PlaceholderPage title="Eval Jobs" description="Run monitor foundation now has routed shell support." />} />
        <Route path="/team" element={<PlaceholderPage title="Team" description="Team management screen is scaffolded for implementation." />} />
        <Route path="/settings" element={<PlaceholderPage title="Settings" description="Workspace settings foundation is available." />} />
      </Routes>
    </AppShell>
  );
}
