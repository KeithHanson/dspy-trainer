/* ============================================================
   Module Bundles — list, upload + validation, detail
   ============================================================ */
function BundlesScreen({ route, nav, toast }) {
  if (route.params.upload) return <BundleUpload nav={nav} toast={toast} />;
  if (route.params.id) return <BundleDetail id={route.params.id} nav={nav} toast={toast} />;
  return <BundleList nav={nav} />;
}

function BundleList({ nav }) {
  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 1000 }}>
        <div className="row between" style={{ marginBottom: 20 }}>
          <div className="col gap-1">
            <div className="t-display" style={{ fontSize: 22 }}>Module Bundles</div>
            <div className="muted t-sm">Agent code packages — <span className="mono">module.py</span> + <span className="mono">metric.py</span> — validated in a sandbox.</div>
          </div>
          <div className="row gap-2">
            <Button icon="download" variant="outline">Example bundle</Button>
            <Button variant="primary" icon="upload" onClick={() => nav('bundle', { upload: true })}>Upload bundle</Button>
          </div>
        </div>

        <div className="col gap-3">
          {DB.bundles.map(b => <BundleRow key={b.id} b={b} nav={nav} />)}
        </div>
      </div>
    </div>
  );
}

function BundleRow({ b, nav }) {
  const errs = b.diagnostics.filter(d => d.level === 'err').length;
  const warns = b.diagnostics.filter(d => d.level === 'warn').length;
  return (
    <div className="panel card-pad row-click" style={{ display: 'flex', gap: 16, alignItems: 'center' }} onClick={() => nav('bundle', { id: b.id })}>
      <div className="center" style={{ width: 40, height: 40, borderRadius: 9, background: 'var(--panel-2)', border: '1px solid var(--border-soft)', color: b.status==='valid'?'var(--accent)':'var(--fail)', flex: 'none' }}>
        <Icon name="box" size={19} />
      </div>
      <div className="col gap-1" style={{ flex: 1, minWidth: 0 }}>
        <div className="row gap-2">
          <span style={{ fontWeight: 600 }}>{b.name}</span>
          <span className="badge b-accent">{b.version}</span>
          <Badge status={b.status} />
        </div>
        <div className="mono t-xs faint" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.signature}</div>
      </div>
      <div className="row gap-4" style={{ flex: 'none' }}>
        {errs > 0 && <span className="row gap-1 mono t-xs" style={{ color: 'var(--fail)' }}><Icon name="alert" size={13} />{errs}</span>}
        {warns > 0 && <span className="row gap-1 mono t-xs" style={{ color: 'var(--warn)' }}><Icon name="info" size={13} />{warns}</span>}
        <div className="col" style={{ alignItems: 'flex-end', lineHeight: 1.3 }}>
          <span className="mono t-xs muted">{b.lmTarget}</span>
          <span className="cap">{b.size} · {ago(b.uploadedAt)}</span>
        </div>
        <Icon name="chevR" size={15} style={{ color: 'var(--text-faint)' }} />
      </div>
    </div>
  );
}

/* ---------------- Upload + validation ---------------- */
const VAL_STEPS = [
  { code: 'unpack', label: 'Unpacking bundle archive', detail: '2 files · module.py, metric.py' },
  { code: 'sandbox', label: 'Spinning up sandbox', detail: 'python 3.11 · dspy 2.5.6' },
  { code: 'module', label: 'Importing module.py', detail: 'TriageAgent(dspy.Module) found' },
  { code: 'metric', label: 'Importing metric.py', detail: 'judge_metric(gold, pred, trace) found' },
  { code: 'signature', label: 'Resolving signature', detail: '2 inputs → 3 outputs' },
  { code: 'smoke', label: 'Smoke-running 1 sample', detail: 'prediction returned in 2.1s' },
];

function BundleUpload({ nav, toast }) {
  const [phase, setPhase] = useState('idle'); // idle | validating | done
  const [over, setOver] = useState(false);
  const [step, setStep] = useState(-1);
  const fileRef = useRef();

  const start = () => {
    setPhase('validating'); setStep(0);
  };
  useEffect(() => {
    if (phase !== 'validating') return;
    if (step >= VAL_STEPS.length) { setPhase('done'); toast({ title: 'Bundle validated', sub: 'support-triage-agent v5 is ready to run', icon: 'check' }); return; }
    const t = setTimeout(() => setStep(s => s + 1), 620);
    return () => clearTimeout(t);
  }, [phase, step]);

  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 720 }}>
        <div className="col gap-1" style={{ marginBottom: 22 }}>
          <div className="t-display" style={{ fontSize: 22 }}>Upload module bundle</div>
          <div className="muted t-sm">We validate imports, signatures, and the metric contract in an isolated sandbox before any eval runs.</div>
        </div>

        {phase === 'idle' && (
          <div className="fade-in col gap-4">
            <div className={`dropzone col center ${over ? 'over' : ''}`} style={{ padding: '44px 24px', gap: 14, cursor: 'pointer' }}
              onClick={start}
              onDragOver={e => { e.preventDefault(); setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={e => { e.preventDefault(); setOver(false); start(); }}>
              <div className="center" style={{ width: 48, height: 48, borderRadius: 12, background: 'var(--panel-2)', border: '1px solid var(--border-soft)', color: 'var(--accent)' }}>
                <Icon name="upload" size={22} />
              </div>
              <div className="col center gap-1">
                <div className="t-h2">Drop your bundle here, or click to browse</div>
                <div className="cap">A <span className="mono">.zip</span> containing <span className="mono">module.py</span> and <span className="mono">metric.py</span> · max 25 MB</div>
              </div>
              <input ref={fileRef} type="file" hidden />
            </div>

            <div className="panel card-pad row between">
              <div className="row gap-3">
                <Icon name="download" size={18} style={{ color: 'var(--accent)' }} />
                <div className="col" style={{ lineHeight: 1.3 }}>
                  <span className="t-sm" style={{ fontWeight: 500 }}>Don't have a bundle yet?</span>
                  <span className="cap">Download a working triage agent to see the expected structure.</span>
                </div>
              </div>
              <Button size="sm" icon="download" onClick={() => toast({ title: 'Downloading example-bundle.zip', icon: 'download' })}>Example</Button>
            </div>

            <div className="col gap-2">
              <div className="t-label">Expected structure</div>
              <pre className="code">{`example-bundle.zip
├── `}<span className="c-key">module.py</span>{`     `}<span className="c-com"># class TriageAgent(dspy.Module)</span>{`
├── `}<span className="c-key">metric.py</span>{`     `}<span className="c-com"># def judge_metric(gold, pred, trace=None)</span>{`
└── `}<span className="c-key">bundle.toml</span>{`   `}<span className="c-com"># name, version, lm_target, deps</span></pre>
            </div>
          </div>
        )}

        {(phase === 'validating' || phase === 'done') && (
          <div className="fade-in col gap-4">
            <div className="panel" style={{ overflow: 'hidden' }}>
              <div className="row between" style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-soft)' }}>
                <div className="row gap-3">
                  <Icon name="file" size={16} style={{ color: 'var(--text-muted)' }} />
                  <span className="mono t-sm">support-triage-agent.zip</span>
                  <span className="cap">38.2 KB</span>
                </div>
                {phase === 'done' ? <Badge status="valid" /> : <Badge status="validating">validating…</Badge>}
              </div>
              <div className="col" style={{ padding: '6px 0' }}>
                {VAL_STEPS.map((s, i) => {
                  const state = phase === 'done' || i < step ? 'done' : i === step ? 'active' : 'wait';
                  return (
                    <div key={s.code} className="row gap-3" style={{ padding: '8px 16px', opacity: state === 'wait' ? 0.4 : 1, transition: 'opacity .3s' }}>
                      <span style={{ width: 18, flex: 'none' }}>
                        {state === 'done' && <Icon name="check" size={15} style={{ color: 'var(--pass)' }} />}
                        {state === 'active' && <Spinner size={14} />}
                        {state === 'wait' && <span className="dot d-draft" style={{ marginLeft: 5 }} />}
                      </span>
                      <span className="t-sm" style={{ flex: 1, fontWeight: state==='active'?500:400 }}>{s.label}</span>
                      {state === 'done' && <span className="mono t-xs faint">{s.detail}</span>}
                    </div>
                  );
                })}
              </div>
            </div>

            {phase === 'done' && (
              <div className="fade-up col gap-4">
                <div className="row gap-3" style={{ padding: 14, borderRadius: 10, background: 'var(--pass-dim)' }}>
                  <Icon name="check" size={18} style={{ color: 'var(--pass)', flex: 'none', marginTop: 1 }} />
                  <div className="col gap-1" style={{ flex: 1 }}>
                    <div className="t-sm" style={{ fontWeight: 600 }}>Validation passed with 1 warning</div>
                    <div className="cap">Signature <span className="mono">ticket, history → category, priority, reply</span> · no random seed set (runs may be non-deterministic).</div>
                  </div>
                </div>
                <div className="row gap-2" style={{ justifyContent: 'flex-end' }}>
                  <Button onClick={() => nav('bundles')}>Back to bundles</Button>
                  <Button variant="primary" iconRight="arrowRight" onClick={() => nav('plan-new')}>Create eval plan</Button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- Bundle detail ---------------- */
function BundleDetail({ id, nav, toast }) {
  const b = DB.getBundle(id);
  const [tab, setTab] = useState('diagnostics');
  if (!b) return null;
  const errs = b.diagnostics.filter(d => d.level === 'err').length;

  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 940 }}>
        <div className="row between" style={{ marginBottom: 20 }}>
          <div className="row gap-3">
            <div className="center" style={{ width: 44, height: 44, borderRadius: 10, background: 'var(--panel-2)', border: '1px solid var(--border-soft)', color: b.status==='valid'?'var(--accent)':'var(--fail)' }}><Icon name="box" size={21} /></div>
            <div className="col gap-1">
              <div className="row gap-2"><span className="t-display" style={{ fontSize: 21 }}>{b.name}</span><span className="badge b-accent">{b.version}</span><Badge status={b.status} /></div>
              <div className="cap mono">{b.size} · uploaded {ago(b.uploadedAt)} by {b.author} · dspy {b.dspyVersion}</div>
            </div>
          </div>
          <div className="row gap-2">
            <Button size="sm" icon="download" variant="ghost" />
            {b.status === 'valid'
              ? <Button variant="primary" iconRight="arrowRight" onClick={() => nav('plan-new', { bundleId: b.id })}>Use in plan</Button>
              : <Button variant="primary" icon="refresh" onClick={() => toast({ title: 'Re-validation queued', icon: 'refresh', tone: 'info' })}>Re-validate</Button>}
          </div>
        </div>

        {/* signature card */}
        <div className="panel card-pad" style={{ marginBottom: 16 }}>
          <div className="t-label" style={{ marginBottom: 9 }}>Signature</div>
          <div className="code" style={{ whiteSpace: 'pre-wrap' }}>{b.signature}</div>
          <div className="row gap-5" style={{ marginTop: 14 }}>
            <MetaItem label="LM target" value={b.lmTarget} />
            <MetaItem label="Module" value="TriageAgent" />
            <MetaItem label="Metric fn" value="judge_metric" />
          </div>
        </div>

        <div className="seg-ctl" style={{ marginBottom: 14 }}>
          {['diagnostics', 'module.py', 'metric.py'].map(t => (
            <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t === 'diagnostics' ? `Diagnostics${errs?` · ${errs}`:''}` : <span className="mono">{t}</span>}</button>
          ))}
        </div>

        {tab === 'diagnostics' && (
          <div className="panel" style={{ overflow: 'hidden' }}>
            {b.diagnostics.map((d, i) => (
              <div key={i} className="row gap-3" style={{ padding: '12px 16px', borderBottom: i < b.diagnostics.length-1 ? '1px solid var(--border-soft)' : 'none' }}>
                <Icon name={d.level==='ok'?'check':d.level==='warn'?'info':'alert'} size={16}
                  style={{ color: d.level==='ok'?'var(--pass)':d.level==='warn'?'var(--warn)':'var(--fail)', flex: 'none', marginTop: 1 }} />
                <div className="col gap-1" style={{ flex: 1 }}>
                  <div className="t-sm">{d.msg}</div>
                  <span className="mono cap">{d.code}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {tab === 'module.py' && <pre className="code">{MODULE_PY}</pre>}
        {tab === 'metric.py' && <pre className="code">{METRIC_PY}</pre>}
      </div>
    </div>
  );
}

function MetaItem({ label, value }) {
  return <div className="col gap-1"><span className="t-label" style={{ fontSize: 9.5 }}>{label}</span><span className="mono t-sm t2">{value}</span></div>;
}

const MODULE_PY = `import dspy

class TriageAgent(dspy.Module):
    def __init__(self):
        self.classify = dspy.ChainOfThought(
            "ticket, history -> category, priority"
        )
        self.draft = dspy.ChainOfThought(
            "ticket, category, priority -> reply"
        )

    def forward(self, ticket, history):
        c = self.classify(ticket=ticket, history=history)
        r = self.draft(ticket=ticket,
                       category=c.category,
                       priority=c.priority)
        return dspy.Prediction(
            category=c.category,
            priority=c.priority,
            reply=r.reply,
        )`;

const METRIC_PY = `def judge_metric(gold, pred, trace=None):
    """Returns a JudgeResult: score + pass/fail + rationale."""
    cat_ok = gold.category == pred.category
    pri_ok = gold.priority == pred.priority
    score = 0.5 * cat_ok + 0.3 * pri_ok + 0.2 * _reply_ok(pred.reply)

    flags = []
    if not cat_ok: flags.append("answer_mismatch")
    if not pri_ok: flags.append("priority_drift")

    return dict(
        score=score,
        passed=score >= 0.7,
        rationale=_explain(gold, pred, cat_ok, pri_ok),
        flags=flags,
    )`;

Object.assign(window, { BundlesScreen });
