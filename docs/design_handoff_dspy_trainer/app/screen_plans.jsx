/* ============================================================
   Evaluation Plans — list + builder
   ============================================================ */
function PlansScreen({ route, nav, toast, onRun }) {
  if (route.name === 'plan-new') return <PlanBuilder route={route} nav={nav} toast={toast} onRun={onRun} />;
  return <PlanList nav={nav} toast={toast} onRun={onRun} />;
}

function PlanList({ nav, toast, onRun }) {
  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 1000 }}>
        <div className="row between" style={{ marginBottom: 20 }}>
          <div className="col gap-1">
            <div className="t-display" style={{ fontSize: 22 }}>Evaluation Plans</div>
            <div className="muted t-sm">Question sets + expected answers — your reusable test decks.</div>
          </div>
          <Button variant="primary" icon="plus" onClick={() => nav('plan-new')}>New plan</Button>
        </div>

        <div className="col gap-3">
          {DB.plans.map(p => {
            const b = DB.getBundle(p.bundleId);
            const tasks = p.questions.length * p.runsPerQuestion;
            const job = DB.getJobByPlan(p.id);
            const pr = job ? DB.passRate(job.items) : null;
            return (
              <div key={p.id} className="panel card-pad row-click" style={{ display: 'flex', gap: 16, alignItems: 'center' }} onClick={() => job ? nav('run', { jobId: job.id }) : nav('plan-new', { id: p.id })}>
                <Icon name="layers" size={20} style={{ color: 'var(--text-muted)', flex: 'none' }} />
                <div className="col gap-1" style={{ flex: 1, minWidth: 0 }}>
                  <div className="row gap-2"><span style={{ fontWeight: 600 }}>{p.name}</span><Badge status={p.status} /></div>
                  <div className="cap mono">{b.name} {b.version} · {p.questions.length} questions × {p.runsPerQuestion} runs = {tasks} tasks · {p.maxWorkers} workers</div>
                </div>
                {pr != null && (
                  <div className="col" style={{ alignItems: 'flex-end', lineHeight: 1.3 }}>
                    <span className="mono t-sm" style={{ color: pr >= 0.7 ? 'var(--pass)' : 'var(--warn)' }}>{(pr*100).toFixed(0)}% pass</span>
                    <span className="cap">{ago(p.createdAt)}</span>
                  </div>
                )}
                {p.status === 'draft' && (
                  <Button size="sm" variant="primary" icon="play" onClick={(e) => { e.stopPropagation(); onRun(p.id); }}>Run</Button>
                )}
                <Icon name="chevR" size={15} style={{ color: 'var(--text-faint)' }} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ---------------- Builder ---------------- */
function PlanBuilder({ route, nav, toast, onRun }) {
  const existing = route.params.id ? DB.getPlan(route.params.id) : null;
  const [name, setName] = useState(existing ? existing.name : '');
  const [bundleId, setBundleId] = useState(route.params.bundleId || (existing && existing.bundleId) || 'bdl_8f2a');
  const [runs, setRuns] = useState(existing ? existing.runsPerQuestion : 3);
  const [workers, setWorkers] = useState(existing ? existing.maxWorkers : 8);
  const [rows, setRows] = useState(existing ? existing.questions.map(q => ({ ...q })) : [
    { id: 'n1', input: '', expected: '' },
  ]);

  const validBundles = DB.bundles.filter(b => b.status === 'valid');
  const filled = rows.filter(r => r.input.trim());
  const tasks = filled.length * runs;
  const canRun = name.trim() && filled.length > 0;

  const addRow = () => setRows(r => [...r, { id: 'n' + Date.now(), input: '', expected: '' }]);
  const upd = (id, key, val) => setRows(r => r.map(x => x.id === id ? { ...x, [key]: val } : x));
  const del = (id) => setRows(r => r.length > 1 ? r.filter(x => x.id !== id) : r);
  const loadSample = () => setRows(DB.QUESTIONS.slice(0, 8).map(q => ({ ...q })));

  const save = (run) => {
    const plan = {
      id: existing ? existing.id : 'pln_' + Date.now().toString(36),
      name: name.trim() || 'Untitled plan', bundleId,
      questions: filled, runsPerQuestion: runs, maxWorkers: workers,
      status: run ? 'queued' : 'draft', createdAt: Date.now(), createdBy: DB.USER.name,
    };
    if (!existing) DB.plans.unshift(plan);
    if (run) { onRun(plan.id, plan); }
    else { toast({ title: 'Plan saved as draft', sub: plan.name, icon: 'check' }); nav('plans'); }
  };

  return (
    <div className="page">
      <div className="page-head row between">
        <div className="col gap-1">
          <div className="t-h1">{existing ? 'Edit plan' : 'New evaluation plan'}</div>
          <div className="cap">Define questions and expected answers, then choose how hard to stress it.</div>
        </div>
        <div className="row gap-2">
          <Button onClick={() => nav('plans')}>Cancel</Button>
          <Button onClick={() => save(false)} disabled={!name.trim()}>Save draft</Button>
          <Button variant="primary" icon="play" onClick={() => save(true)} disabled={!canRun}>Save & run</Button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'stretch', flex: 1, minHeight: 0 }}>
        {/* main */}
        <div className="scroll-y" style={{ flex: 1, padding: '24px 28px 80px' }}>
          <div style={{ maxWidth: 760 }}>
            <div className="col gap-4" style={{ marginBottom: 26 }}>
              <div>
                <label className="field-label">Plan name</label>
                <input className="input" placeholder="e.g. Triage v4 — regression deck" value={name} onChange={e => setName(e.target.value)} />
              </div>
              <div>
                <label className="field-label">Module bundle under test</label>
                <div className="row gap-2 wrap">
                  {validBundles.map(b => (
                    <button key={b.id} className="row gap-2" onClick={() => setBundleId(b.id)} style={{
                      padding: '9px 12px', borderRadius: 8, textAlign: 'left',
                      border: `1px solid ${bundleId===b.id?'var(--accent-line)':'var(--border)'}`,
                      background: bundleId===b.id?'var(--accent-dim)':'var(--bg-deep)' }}>
                      <Icon name="box" size={15} style={{ color: bundleId===b.id?'var(--accent)':'var(--text-faint)' }} />
                      <div className="col" style={{ lineHeight: 1.25 }}>
                        <span className="t-sm" style={{ fontWeight: 500 }}>{b.name} <span className="mono faint">{b.version}</span></span>
                        <span className="cap mono">{b.lmTarget}</span>
                      </div>
                      {bundleId===b.id && <Icon name="check" size={14} style={{ color: 'var(--accent)' }} />}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="row between" style={{ marginBottom: 12 }}>
              <div className="row gap-2"><span className="t-h2">Questions</span><span className="badge b-muted">{filled.length}</span></div>
              <div className="row gap-2">
                <Button size="sm" variant="ghost" icon="copy" onClick={loadSample}>Load sample set</Button>
                <Button size="sm" icon="plus" onClick={addRow}>Add question</Button>
              </div>
            </div>

            <div className="col gap-2">
              <div className="row gap-3" style={{ padding: '0 4px' }}>
                <span className="t-label" style={{ width: 22 }}>#</span>
                <span className="t-label" style={{ flex: 1 }}>Input prompt (Label Payload in)</span>
                <span className="t-label" style={{ flex: 1 }}>Expected answer (gold target)</span>
                <span style={{ width: 28 }} />
              </div>
              {rows.map((r, i) => (
                <div key={r.id} className="row gap-3" style={{ alignItems: 'flex-start' }}>
                  <span className="mono t-xs faint" style={{ width: 22, paddingTop: 9, textAlign: 'right' }}>{i+1}</span>
                  <textarea className="textarea mono" style={{ flex: 1, minHeight: 38 }} rows={2} placeholder="A user message or task input…" value={r.input} onChange={e => upd(r.id, 'input', e.target.value)} />
                  <textarea className="textarea mono" style={{ flex: 1, minHeight: 38 }} rows={2} placeholder="category=… priority=… reply mentions…" value={r.expected} onChange={e => upd(r.id, 'expected', e.target.value)} />
                  <Button size="sm" variant="ghost" icon="trash" style={{ marginTop: 3 }} onClick={() => del(r.id)} />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* config rail */}
        <div className="col" style={{ width: 312, flex: 'none', borderLeft: '1px solid var(--border-soft)', background: 'var(--bg-deep)', padding: 22, gap: 22, overflowY: 'auto' }}>
          <div className="col gap-1"><div className="t-label">Agent Run Plan</div><div className="cap">Stress / repeat configuration</div></div>

          <Stepper label="Runs per question" hint="Repeat each question to catch non-determinism" value={runs} setValue={setRuns} min={1} max={20} icon="refresh" />
          <Stepper label="Max workers" hint="Concurrency cap — tasks running in parallel" value={workers} setValue={setWorkers} min={1} max={24} icon="cpu" />

          <div className="hr" />

          <div className="col gap-3">
            <div className="t-label">Estimated workload</div>
            <div className="panel card-pad col gap-3" style={{ background: 'var(--panel)' }}>
              <Calc label="Questions" value={filled.length} />
              <Calc label="× Runs per question" value={runs} />
              <div className="hr" />
              <div className="row between"><span className="t-sm" style={{ fontWeight: 500 }}>Total Agent Run Tasks</span><span className="stat-val" style={{ color: 'var(--accent)', fontSize: 17 }}>{tasks}</span></div>
              <div className="row between"><span className="cap">Est. wall-clock @ {workers} workers</span><span className="mono t-xs t2">~{tasks ? Math.max(1, Math.ceil(tasks / workers * 2.8 / 60)) : 0} min</span></div>
            </div>
          </div>

          <div className="row gap-2" style={{ padding: 11, borderRadius: 8, background: 'var(--info-dim)' }}>
            <Icon name="info" size={15} style={{ color: 'var(--info)', flex: 'none', marginTop: 1 }} />
            <span className="cap">Each task opens an MLflow trace under one parent run. Judge results attach automatically.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stepper({ label, hint, value, setValue, min, max, icon }) {
  return (
    <div className="col gap-2">
      <div className="row gap-2"><Icon name={icon} size={14} style={{ color: 'var(--text-faint)', flex: 'none' }} /><span className="field-label" style={{ margin: 0, whiteSpace: 'nowrap' }}>{label}</span></div>
      <div className="row gap-2">
        <Button size="sm" icon="x" style={{ width: 32 }} onClick={() => setValue(Math.max(min, value - 1))}><span style={{ fontSize: 16, marginTop: -1 }}>−</span></Button>
        <div className="center mono" style={{ flex: 1, height: 32, border: '1px solid var(--border)', borderRadius: 6, background: 'var(--bg)', fontWeight: 600, fontSize: 15 }}>{value}</div>
        <Button size="sm" icon="plus" style={{ width: 32 }} onClick={() => setValue(Math.min(max, value + 1))} />
      </div>
      <span className="cap">{hint}</span>
    </div>
  );
}
function Calc({ label, value }) {
  return <div className="row between"><span className="t-sm muted">{label}</span><span className="mono t-sm">{value}</span></div>;
}

Object.assign(window, { PlansScreen });
