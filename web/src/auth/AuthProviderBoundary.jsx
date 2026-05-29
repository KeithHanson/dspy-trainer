import { Auth0Provider } from "@auth0/auth0-react";
import { EmptyState } from "../components/states/EmptyState";
import { auth0Config, hasAuth0Config } from "./auth0-config";

function MissingAuth0ConfigState() {
  return (
    <main className="auth-missing center">
      <div className="auth-missing-card">
        <EmptyState
          title="Authentication not configured"
          description="Set VITE_AUTH0_DOMAIN and VITE_AUTH0_CLIENT_ID to enable login."
        />
      </div>
    </main>
  );
}

export function AuthProviderBoundary({ children }) {
  if (!hasAuth0Config()) {
    return <MissingAuth0ConfigState />;
  }

  return (
    <Auth0Provider
      domain={auth0Config.domain}
      clientId={auth0Config.clientId}
      cacheLocation={auth0Config.cacheLocation}
      useRefreshTokens={auth0Config.useRefreshTokens}
      onRedirectCallback={(appState) => {
        const target = appState?.returnTo || window.location.pathname;
        window.history.replaceState({}, document.title, target);
      }}
      authorizationParams={auth0Config.authorizationParams}
    >
      {children}
    </Auth0Provider>
  );
}
