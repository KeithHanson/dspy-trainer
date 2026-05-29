import { useMemo, useState } from "react";
import { Icon } from "../components/Icon";
import { Button } from "../components/primitives/Button";
import { auth0Config } from "./auth0-config";

const PROVIDERS = [
  { id: "github", label: "Continue with GitHub", connectionId: "github", icon: "github" },
  { id: "google", label: "Continue with Google", connectionId: "google", icon: "google" },
  { id: "microsoft", label: "Microsoft", connectionId: "microsoft", icon: "microsoft" },
  { id: "sso", label: "SSO / SAML", connectionId: "sso", icon: "shield" },
];

function ProviderButton({ provider, pendingProvider, onClick, block }) {
  return (
    <button
      className={`btn btn-lg btn-outline ${block ? "btn-block" : ""}`}
      disabled={Boolean(pendingProvider)}
      onClick={() => onClick(provider)}
      style={{ justifyContent: "flex-start", gap: 11, position: "relative", ...(block ? {} : { flex: 1 }) }}
      type="button"
    >
      <Icon name={provider.icon} size={16} />
      <span style={{ fontWeight: 500 }}>{provider.label}</span>
      {pendingProvider === provider.id ? <span className="auth-spinner" aria-hidden="true" /> : null}
    </button>
  );
}

export function AuthScreen({ loginWithRedirect, orgName = "Default" }) {
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [pendingProvider, setPendingProvider] = useState(null);
  const [authError, setAuthError] = useState("");

  const heading = mode === "signin" ? "Sign in to your workspace" : "Create your account";
  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const subtitle = useMemo(
    () =>
      mode === "signin"
        ? "Run, judge, and stress-test your DSPy agents."
        : `You've been invited to ${orgName}. Pick a provider to continue.`,
    [mode, orgName],
  );

  const startLogin = async (provider) => {
    setPendingProvider(provider.id);
    setAuthError("");
    try {
      const connection = auth0Config.connections?.[provider.connectionId];
      await loginWithRedirect({
        appState: { returnTo },
        authorizationParams: {
          ...(connection ? { connection } : {}),
          ...(mode === "signup" ? { screen_hint: "signup" } : {}),
        },
      });
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Authentication failed. Please try again.");
      setPendingProvider(null);
    }
  };

  const loginWithEmail = async () => {
    if (!email.trim()) {
      setAuthError("Enter your email to continue.");
      return;
    }
    setPendingProvider("email");
    setAuthError("");
    try {
      await loginWithRedirect({
        appState: { returnTo },
        authorizationParams: {
          login_hint: email.trim(),
          ...(mode === "signup" ? { screen_hint: "signup" } : {}),
        },
      });
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Authentication failed. Please try again.");
      setPendingProvider(null);
    }
  };

  return (
    <div className="auth-root">
      <div className="col center auth-form-panel">
        <div className="fade-up auth-form-wrap">
          <div className="row gap-3 auth-brand">
            <div className="center auth-brand-mark">
              <Icon name="bolt" size={19} />
            </div>
            <div className="col auth-brand-copy">
              <div className="auth-brand-name">dspy-trainer</div>
              <div className="t-label auth-brand-org">{orgName}</div>
            </div>
          </div>

          <div className="t-display auth-title">{heading}</div>
          <div className="muted t-sm auth-subtitle">{subtitle}</div>

          <div className="col gap-2">
            <ProviderButton provider={PROVIDERS[0]} pendingProvider={pendingProvider} onClick={startLogin} block />
            <ProviderButton provider={PROVIDERS[1]} pendingProvider={pendingProvider} onClick={startLogin} block />
            <div className="row gap-2 auth-provider-row">
              <ProviderButton provider={PROVIDERS[2]} pendingProvider={pendingProvider} onClick={startLogin} />
              <ProviderButton provider={PROVIDERS[3]} pendingProvider={pendingProvider} onClick={startLogin} />
            </div>
          </div>

          <div className="row gap-3 auth-divider-wrap">
            <div className="hr auth-divider" />
            <span className="t-label auth-divider-label">or</span>
            <div className="hr auth-divider" />
          </div>

          <div className="col gap-2">
            <input
              className="input auth-email"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@coherelabs.ai"
              type="email"
              value={email}
            />
            <Button className="btn-block" onClick={loginWithEmail} size="lg" variant="primary">
              {mode === "signin" ? "Continue with email" : "Send invite link"}
            </Button>
          </div>

          {authError ? <p className="auth-error t-sm">{authError}</p> : null}

          <div className="t-sm muted auth-mode-toggle">
            {mode === "signin" ? (
              <>
                New here?{" "}
                <button className="auth-link" onClick={() => setMode("signup")} type="button">
                  Request access
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button className="auth-link" onClick={() => setMode("signin")} type="button">
                  Sign in
                </button>
              </>
            )}
          </div>

          <div className="row center gap-2 auth-trust">
            <Icon className="faint" name="shield" size={12} />
            <span className="cap">Secured by Auth0 · SOC 2 Type II</span>
          </div>
        </div>
      </div>

      <div className="col auth-preview-panel">
        <div className="auth-preview-bg" />
        <div className="col auth-preview-content">
          <div className="t-label">Live eval monitor</div>
          <div className="card auth-preview-card">
            <div className="row between auth-preview-head">
              <div className="row gap-2">
                <span className="dot d-live" />
                <span className="t-sm auth-preview-run-name">Triage v4 - regression deck</span>
              </div>
              <span className="t-label">running</span>
            </div>
            <div className="row between t-sm muted">
              <span>24 / 36 tasks</span>
              <span className="auth-preview-pass">79% pass</span>
            </div>
            <div className="prog auth-preview-progress">
              <i style={{ width: "79%" }} />
            </div>
          </div>
          <div className="t-h1 auth-preview-title">
            Upload a module bundle. Write an eval plan. Watch every run get judged in real time.
          </div>
          <div className="col gap-3">
            <div className="row gap-3">
              <Icon className="auth-feature-icon" name="box" size={15} />
              <span className="t-sm">Validate module.py + metric.py in a sandbox before you run</span>
            </div>
            <div className="row gap-3">
              <Icon className="auth-feature-icon" name="layers" size={15} />
              <span className="t-sm">Repeat each question N times across M parallel workers</span>
            </div>
            <div className="row gap-3">
              <Icon className="auth-feature-icon" name="activity" size={15} />
              <span className="t-sm">Per-item pass/fail, judge rationale, and MLflow traces</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
