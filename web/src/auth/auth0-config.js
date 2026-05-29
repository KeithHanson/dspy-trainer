const rawDomain = import.meta.env.VITE_AUTH0_DOMAIN;
const rawClientId = import.meta.env.VITE_AUTH0_CLIENT_ID;
const rawAudience = import.meta.env.VITE_AUTH0_AUDIENCE;
const rawGithubConnection = import.meta.env.VITE_AUTH0_CONNECTION_GITHUB;
const rawGoogleConnection = import.meta.env.VITE_AUTH0_CONNECTION_GOOGLE;
const rawMicrosoftConnection = import.meta.env.VITE_AUTH0_CONNECTION_MICROSOFT;
const rawSsoConnection = import.meta.env.VITE_AUTH0_CONNECTION_SSO;
const rawCacheLocation = import.meta.env.VITE_AUTH0_CACHE_LOCATION;
const rawUseRefreshTokens = import.meta.env.VITE_AUTH0_USE_REFRESH_TOKENS;

const domain = typeof rawDomain === "string" ? rawDomain.trim() : "";
const clientId = typeof rawClientId === "string" ? rawClientId.trim() : "";
const audience = typeof rawAudience === "string" ? rawAudience.trim() : "";
const githubConnection = typeof rawGithubConnection === "string" ? rawGithubConnection.trim() : "";
const googleConnection = typeof rawGoogleConnection === "string" ? rawGoogleConnection.trim() : "";
const microsoftConnection = typeof rawMicrosoftConnection === "string" ? rawMicrosoftConnection.trim() : "";
const ssoConnection = typeof rawSsoConnection === "string" ? rawSsoConnection.trim() : "";
const cacheLocation = rawCacheLocation === "memory" ? "memory" : "localstorage";
const useRefreshTokens = rawUseRefreshTokens === "true";

export const auth0Config = {
  domain,
  clientId,
  authorizationParams: {
    redirect_uri: window.location.origin,
    ...(audience ? { audience } : {}),
    scope: "openid profile email",
  },
  connections: {
    github: githubConnection,
    google: googleConnection,
    microsoft: microsoftConnection,
    sso: ssoConnection,
  },
  cacheLocation,
  useRefreshTokens,
};

export function hasAuth0Config() {
  return Boolean(auth0Config.domain && auth0Config.clientId);
}
