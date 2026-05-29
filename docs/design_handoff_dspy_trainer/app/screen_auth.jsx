/* ============================================================
   Auth screen — Auth0-style hosted login, OAuth + email
   ============================================================ */
function AuthScreen({ onAuth }) {
  const [mode, setMode] = useState('signin'); // signin | signup
  const [email, setEmail] = useState('');
  const [pending, setPending] = useState(null);

  const oauth = (provider) => {
    setPending(provider);
    setTimeout(() => onAuth(provider), 1100);
  };

  return (
    <div style={{ display: 'flex', alignItems: 'stretch', height: '100%', background: 'var(--bg-deep)' }}>
      {/* left: form */}
      <div className="col center" style={{ flex: 1, minWidth: 0, position: 'relative' }}>
        <div className="fade-up" style={{ width: 360, maxWidth: 'calc(100vw - 48px)' }}>
          <div className="row gap-3" style={{ marginBottom: 28 }}>
            <div className="center" style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--accent)', color: 'var(--accent-ink)' }}>
              <Icon name="bolt" size={19} strokeWidth={2.2} />
            </div>
            <div className="col" style={{ lineHeight: 1.2 }}>
              <div style={{ fontSize: 15, fontWeight: 600 }}>dspy-trainer</div>
              <div className="t-label" style={{ fontSize: 10 }}>{DB.ORG.name}</div>
            </div>
          </div>

          <div className="t-display" style={{ fontSize: 23, marginBottom: 6 }}>
            {mode === 'signin' ? 'Sign in to your workspace' : 'Create your account'}
          </div>
          <div className="muted t-sm" style={{ marginBottom: 26 }}>
            {mode === 'signin'
              ? 'Run, judge, and stress-test your DSPy agents.'
              : 'You\u2019ve been invited to ' + DB.ORG.name + '. Pick a provider to continue.'}
          </div>

          <div className="col gap-2">
            <button className="btn btn-lg btn-block btn-outline" style={{ justifyContent: 'flex-start', gap: 11, position: 'relative' }} onClick={() => oauth('github')} disabled={!!pending}>
              <Icon name="github" size={18} />
              <span style={{ fontWeight: 500 }}>Continue with GitHub</span>
              {pending === 'github' && <Spinner />}
            </button>
            <button className="btn btn-lg btn-block btn-outline" style={{ justifyContent: 'flex-start', gap: 11, position: 'relative' }} onClick={() => oauth('google')} disabled={!!pending}>
              <Icon name="google" size={18} />
              <span style={{ fontWeight: 500 }}>Continue with Google</span>
              {pending === 'google' && <Spinner />}
            </button>
            <div className="row gap-2" style={{ marginTop: 2 }}>
              <button className="btn btn-lg btn-outline" style={{ flex: 1, gap: 9 }} onClick={() => oauth('microsoft')} disabled={!!pending}>
                <MsIcon /><span style={{ fontWeight: 500 }}>Microsoft</span>
              </button>
              <button className="btn btn-lg btn-outline" style={{ flex: 1, gap: 9 }} onClick={() => oauth('sso')} disabled={!!pending}>
                <Icon name="shield" size={16} /><span style={{ fontWeight: 500 }}>SSO / SAML</span>
              </button>
            </div>
          </div>

          <div className="row gap-3" style={{ margin: '20px 0' }}>
            <div className="hr" style={{ flex: 1 }} /><span className="t-label" style={{ fontSize: 10 }}>or</span><div className="hr" style={{ flex: 1 }} />
          </div>

          <div className="col gap-2">
            <input className="input" style={{ height: 40 }} placeholder="you@coherelabs.ai" value={email}
              onChange={e => setEmail(e.target.value)} type="email" />
            <Button variant="primary" size="lg" className="btn-block" iconRight="arrowRight"
              onClick={() => oauth('email')} disabled={!!pending}>
              {mode === 'signin' ? 'Continue with email' : 'Send invite link'}
            </Button>
          </div>

          <div className="t-sm muted" style={{ marginTop: 22, textAlign: 'center' }}>
            {mode === 'signin' ? (
              <>New here? <span className="lnk" onClick={() => setMode('signup')}>Request access</span></>
            ) : (
              <>Already have an account? <span className="lnk" onClick={() => setMode('signin')}>Sign in</span></>
            )}
          </div>

          <div className="row center gap-2" style={{ marginTop: 40 }}>
            <Icon name="shield" size={12} style={{ color: 'var(--text-faint)' }} />
            <span className="cap">Secured by Auth0 · SOC 2 Type II</span>
          </div>
        </div>
      </div>

      {/* right: brand / preview panel */}
      <div className="col" style={{ width: '46%', maxWidth: 720, flex: 'none', borderLeft: '1px solid var(--border-soft)', background: 'var(--bg)', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(120% 80% at 80% 0%, var(--accent-dim), transparent 55%)' }} />
        <div className="col" style={{ position: 'relative', padding: 48, height: '100%', justifyContent: 'center', gap: 22 }}>
          <div className="t-label">Live eval monitor</div>
          <MiniRunPreview />
          <div className="t-h1" style={{ maxWidth: 380, lineHeight: 1.35 }}>
            Upload a module bundle. Write an eval plan. Watch every run get judged in real time.
          </div>
          <div className="col gap-3" style={{ marginTop: 4 }}>
            {[
              ['box', 'Validate module.py + metric.py in a sandbox before you run'],
              ['layers', 'Repeat each question N times across M parallel workers'],
              ['gauge', 'Per-item pass/fail, judge rationale, and MLflow traces'],
            ].map(([ic, tx]) => (
              <div className="row gap-3" key={tx}>
                <Icon name={ic} size={15} style={{ color: 'var(--accent)', marginTop: 2, flex: 'none' }} />
                <span className="t-sm t2">{tx}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Spinner({ size = 15 }) {
  return <span style={{ position: 'absolute', right: 14, width: size, height: size, border: '2px solid var(--border-strong)', borderTopColor: 'var(--text)', borderRadius: '50%', animation: 'spin .7s linear infinite' }} />;
}
function MsIcon() {
  return <svg width="15" height="15" viewBox="0 0 24 24"><rect x="1" y="1" width="10" height="10" fill="#F25022"/><rect x="13" y="1" width="10" height="10" fill="#7FBA00"/><rect x="1" y="13" width="10" height="10" fill="#00A4EF"/><rect x="13" y="13" width="10" height="10" fill="#FFB900"/></svg>;
}

function MiniRunPreview() {
  const [c, setC] = useState({ pass: 18, fail: 4, total: 36 });
  useEffect(() => {
    const t = setInterval(() => setC(p => {
      if (p.pass + p.fail >= p.total) return { pass: 18, fail: 4, total: 36 };
      const pass = Math.random() > 0.25;
      return { ...p, pass: p.pass + (pass ? 1 : 0), fail: p.fail + (pass ? 0 : 1) };
    }), 1400);
    return () => clearInterval(t);
  }, []);
  const done = c.pass + c.fail;
  return (
    <div className="card" style={{ padding: 16, background: 'var(--panel)', boxShadow: 'var(--sh)' }}>
      <div className="row between" style={{ marginBottom: 12 }}>
        <div className="row gap-2"><span className="dot d-live" /><span className="t-sm" style={{ fontWeight: 500 }}>Triage v4 — regression deck</span></div>
        <Badge status="running" />
      </div>
      <div className="row between" style={{ marginBottom: 7 }}>
        <span className="cap">{done} / {c.total} tasks</span>
        <span className="mono t-xs" style={{ color: 'var(--pass)' }}>{Math.round(c.pass / Math.max(done,1) * 100)}% pass</span>
      </div>
      <SegProgress pass={c.pass} fail={c.fail} running={Math.min(8, c.total - done)} total={c.total} />
      <div className="row gap-4" style={{ marginTop: 14 }}>
        <Stat label="pass" val={c.pass} tone="pass" />
        <Stat label="fail" val={c.fail} tone="fail" />
        <Stat label="workers" val="8" tone="run" />
      </div>
    </div>
  );
}
function Stat({ label, val, tone }) {
  return <div className="col gap-1">
    <span className="stat-val" style={{ fontSize: 18, color: tone ? `var(--${tone})` : 'var(--text)' }}>{val}</span>
    <span className="t-label" style={{ fontSize: 9.5 }}>{label}</span>
  </div>;
}

const __spinStyle = document.createElement('style');
__spinStyle.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
document.head.appendChild(__spinStyle);

Object.assign(window, { AuthScreen });
