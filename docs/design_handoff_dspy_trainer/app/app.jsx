/* ============================================================
   Settings (lightweight) + Root App + live simulation engine
   ============================================================ */
function SettingsScreen({ toast }) {
  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 720 }}>
        <div className="col gap-1" style={{ marginBottom: 22 }}>
          <div className="t-display" style={{ fontSize: 22 }}>Settings</div>
          <div className="muted t-sm">Workspace configuration for {DB.ORG.name}.</div>
        </div>

        <SettingsGroup label="Workspace">
          <SettingRow label="Workspace name" hint="Shown across the app and on invites.">
            <input className="input" defaultValue={DB.ORG.name} style={{ width: 240 }} />
          </SettingRow>
          <SettingRow label="Default LM target" hint="Used when a bundle doesn't pin its own.">
            <input className="input mono" defaultValue="openai/gpt-4o-mini" style={{ width: 240 }} />
          </SettingRow>
          <SettingRow label="Sandbox region" hint="Where eval workers run.">
            <div className="seg-ctl"><button className="active">us-east</button><button>eu-west</button></div>
          </SettingRow>
        </SettingsGroup>

        <SettingsGroup label="Connections">
          {[['github','GitHub','Connected · 4 members'],['google','Google Workspace','Connected · domain coherelabs.ai'],['shield','MLflow tracking server','https://mlflow.coherelabs.ai']].map(([ic,t,s]) => (
            <SettingRow key={t} label={<span className="row gap-2"><Icon name={ic} size={15} />{t}</span>} hint={s}>
              <Badge status="active">connected</Badge>
            </SettingRow>
          ))}
        </SettingsGroup>

        <SettingsGroup label="Run defaults">
          <SettingRow label="Default runs per question" hint="Applied to new plans.">
            <input className="input mono" defaultValue="3" style={{ width: 80 }} />
          </SettingRow>
          <SettingRow label="Default max workers" hint="Concurrency cap per plan.">
            <input className="input mono" defaultValue="8" style={{ width: 80 }} />
          </SettingRow>
        </SettingsGroup>

        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 18 }}>
          <Button variant="primary" onClick={() => toast({ title: 'Settings saved', icon: 'check' })}>Save changes</Button>
        </div>
      </div>
    </div>
  );
}
function SettingsGroup({ label, children }) {
  return <div className="panel" style={{ marginBottom: 18, overflow: 'hidden' }}>
    <div className="t-label" style={{ padding: '13px 18px', borderBottom: '1px solid var(--border-soft)' }}>{label}</div>
    {children}
  </div>;
}
function SettingRow({ label, hint, children }) {
  return <div className="row between" style={{ padding: '14px 18px', borderBottom: '1px solid var(--border-soft)', gap: 20 }}>
    <div className="col gap-1"><span className="t-sm" style={{ fontWeight: 500 }}>{label}</span><span className="cap">{hint}</span></div>
    {children}
  </div>;
}

/* ---------------- simulation helpers ---------------- */
function resolveItem(it) {
  const seed = it.qIndex * 7 + (it.attempt - 1) * 3 + 1;
  const passLikely = (seed % 10) < 7;
  const pass = passLikely && !(seed % 13 === 0);
  it.status = pass ? 'pass' : 'fail';
  it.score = pass ? +(0.82 + (seed % 18) / 100).toFixed(2) : +((seed % 40) / 100).toFixed(2);
  it.durationMs = 1400 + (seed * 137) % 4200;
  it.prediction = DB.samplePrediction({ id: it.qId, input: it.input, expected: it.expected }, pass);
  it.rationale = pass ? DB.RATIONALES_PASS[seed % DB.RATIONALES_PASS.length] : DB.RATIONALES_FAIL[seed % DB.RATIONALES_FAIL.length];
  it.flags = pass ? [] : [DB.FLAGS_POOL[seed % DB.FLAGS_POOL.length], ...(seed % 3 === 0 ? [DB.FLAGS_POOL[(seed + 2) % DB.FLAGS_POOL.length]] : [])];
  it._flash = Date.now();
  it._age = 0;
}
function tickJob(job) {
  const items = job.items.map(i => ({ ...i }));
  const running = items.filter(i => i.status === 'running');
  running.forEach(i => { i._age = (i._age || 0) + 1; });
  const toResolve = Math.max(1, Math.round(running.length * 0.3));
  running.filter(i => i._age >= 1).sort((a, b) => (b._age || 0) - (a._age || 0)).slice(0, toResolve).forEach(resolveItem);
  let slots = job.maxWorkers - items.filter(i => i.status === 'running').length;
  for (const it of items) { if (slots <= 0) break; if (it.status === 'pending') { it.status = 'running'; it._age = 0; slots--; } }
  const c = DB.counts(items);
  return { items, done: c.running === 0 && c.pending === 0 };
}
function buildPendingItems(plan) {
  const items = [];
  plan.questions.forEach((q, qi) => {
    for (let a = 0; a < plan.runsPerQuestion; a++) {
      items.push({ id: `ri_job_${plan.id}_${qi}_${a}`, jobId: 'job_' + plan.id, qId: q.id, qIndex: qi, attempt: a + 1,
        input: q.input, expected: q.expected, status: 'pending', score: null, durationMs: null,
        traceId: 'tr_' + Math.abs(qi * 131 + a * 17 + 9001).toString(16), prediction: null, rationale: null, flags: [] });
    }
  });
  return items;
}
function liveFromJob(j) {
  const plan = DB.getPlan(j.planId);
  return { jobId: j.id, planId: j.planId, bundleId: j.bundleId, name: plan.name, status: 'running',
    startedAt: j.startedAt, maxWorkers: j.maxWorkers, runsPerQuestion: j.runsPerQuestion,
    mlflowParent: j.mlflowParent, items: j.items.map(i => ({ ...i })) };
}

/* ---------------- Root ---------------- */
function App() {
  const [authed, setAuthed] = useState(false);
  const [route, setRoute] = useState({ name: 'dashboard', params: {} });
  const [live, setLive] = useState(() => {
    const j = DB.jobs.find(x => x.status === 'running');
    return j ? liveFromJob(j) : null;
  });
  const toast = useToast();

  const nav = useCallback((name, params = {}) => {
    if (name === '__logout') { setAuthed(false); return; }
    const map = { bundle: 'bundles', 'plan-new': 'plans', run: 'runs' };
    setRoute({ name, params });
    document.querySelector('.page-body')?.scrollTo(0, 0);
  }, []);

  // live tick — only runs once signed in, so the run is fresh on arrival
  useEffect(() => {
    if (!authed || !live || live.status !== 'running') return;
    const t = setInterval(() => {
      setLive(prev => {
        if (!prev || prev.status !== 'running') return prev;
        const { items, done } = tickJob(prev);
        if (done) {
          const dbj = DB.getJob(prev.jobId); if (dbj) { dbj.status = 'succeeded'; dbj.items = items; }
          const pl = DB.getPlan(prev.planId); if (pl) pl.status = 'succeeded';
          setTimeout(() => toast({ title: 'Eval job complete', sub: `${prev.name} · all tasks judged`, icon: 'check' }), 0);
          return { ...prev, items, status: 'succeeded' };
        }
        return { ...prev, items };
      });
    }, 1100);
    return () => clearInterval(t);
  }, [authed, live && live.status, live && live.jobId]);

  const onRun = useCallback((planId, planObj) => {
    const plan = planObj || DB.getPlan(planId);
    const jobId = 'job_' + plan.id;
    let dbj = DB.getJob(jobId);
    const items = buildPendingItems(plan);
    if (!dbj) {
      dbj = { id: jobId, planId: plan.id, bundleId: plan.bundleId, status: 'running', startedAt: Date.now(),
        runsPerQuestion: plan.runsPerQuestion, maxWorkers: plan.maxWorkers, items, mlflowParent: 'run_' + plan.id.replace('pln_', '') };
      DB.jobs.unshift(dbj);
    } else { dbj.status = 'running'; dbj.startedAt = Date.now(); dbj.items = items; }
    plan.status = 'running';
    setLive({ jobId, planId: plan.id, bundleId: plan.bundleId, name: plan.name, status: 'running',
      startedAt: Date.now(), maxWorkers: plan.maxWorkers, runsPerQuestion: plan.runsPerQuestion,
      mlflowParent: dbj.mlflowParent, items });
    nav('run', { jobId });
    toast({ title: 'Eval job started', sub: `${plan.name} · ${plan.questions.length * plan.runsPerQuestion} tasks queued`, icon: 'play' });
  }, [nav, toast]);

  const controls = {
    pause: () => setLive(l => ({ ...l, status: 'paused' })),
    resume: () => setLive(l => ({ ...l, status: 'running' })),
    stop: () => { setLive(l => { const dbj = DB.getJob(l.jobId); if (dbj) dbj.status = 'failed'; return { ...l, status: 'failed' }; }); toast({ title: 'Job stopped', tone: 'fail', icon: 'x' }); },
    rerun: (planId) => onRun(planId),
  };

  if (!authed) return <AuthScreen onAuth={() => { setAuthed(true); setLive(l => l ? { ...l, startedAt: Date.now() } : l); setRoute({ name: 'dashboard', params: {} }); }} />;

  const crumbsFor = () => {
    const base = [{ label: DB.ORG.name }];
    const N = { dashboard: 'Overview', bundles: 'Module Bundles', bundle: 'Module Bundles', plans: 'Evaluation Plans', 'plan-new': 'Evaluation Plans', runs: 'Eval Jobs', run: 'Eval Jobs', team: 'Team', settings: 'Settings' };
    const top = N[route.name] || '';
    const topKey = { bundle: 'bundles', 'plan-new': 'plans', run: 'runs' }[route.name] || route.name;
    base.push({ label: top, onClick: () => nav(topKey) });
    if (route.name === 'bundle' && route.params.id) base.push({ label: DB.getBundle(route.params.id)?.name || '' });
    if (route.name === 'bundle' && route.params.upload) base.push({ label: 'Upload' });
    if (route.name === 'plan-new') base.push({ label: route.params.id ? 'Edit' : 'New plan' });
    if (route.name === 'run' && route.params.jobId) { const j = DB.getJob(route.params.jobId); base.push({ label: j ? DB.getPlan(j.planId)?.name : '' }); }
    return base;
  };

  const liveForNav = live && live.status === 'running' ? live : null;

  let screen;
  switch (route.name) {
    case 'dashboard': screen = <Dashboard nav={nav} liveJob={liveForNav} />; break;
    case 'bundles': case 'bundle': screen = <BundlesScreen route={route} nav={nav} toast={toast} />; break;
    case 'plans': case 'plan-new': screen = <PlansScreen route={route} nav={nav} toast={toast} onRun={onRun} />; break;
    case 'runs': case 'run': screen = <RunsScreen route={route} nav={nav} live={live} controls={controls} />; break;
    case 'team': screen = <TeamScreen nav={nav} toast={toast} />; break;
    case 'settings': screen = <SettingsScreen toast={toast} />; break;
    default: screen = <Dashboard nav={nav} liveJob={liveForNav} />;
  }

  return <AppShell route={route} nav={nav} crumbs={crumbsFor()} liveJob={liveForNav}>{screen}</AppShell>;
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <ToastHost><App /></ToastHost>
);
