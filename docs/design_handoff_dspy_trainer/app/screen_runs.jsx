/* ============================================================
   Eval Jobs — list + LIVE run monitor (hero) + run-item drawer
   ============================================================ */
function RunsScreen({ route, nav, live, controls }) {
  if (route.params.jobId) return <RunDetail jobId={route.params.jobId} nav={nav} live={live} controls={controls} />;
  return <JobList nav={nav} live={live} />;
}

function JobList({ nav, live }) {
  const jobs = DB.jobs.slice().sort((a, b) => b.startedAt - a.startedAt);
  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 1000 }}>
        <div className="col gap-1" style={{ marginBottom: 20 }}>
          <div className="t-display" style={{ fontSize: 22 }}>Eval Jobs</div>
          <div className="muted t-sm">Each job is one execution of a module bundle against an evaluation plan.</div>
        </div>
        <div className="panel" style={{ overflow: 'hidden' }}>
          <table className="tbl">
            <thead><tr><th>Plan</th><th>Status</th><th style={{ width: 220 }}>Progress</th><th>Pass</th><th>Avg score</th><th>Started</th><th></th></tr></thead>
            <tbody>
              {jobs.map(j => {
                const useLive = live && live.jobId === j.id;
                const items = useLive ? live.items : j.items;
                const status = useLive ? live.status : j.status;
                const c = DB.counts(items); const pr = DB.passRate(items);
                const plan = DB.getPlan(j.planId);
                return (
                  <tr key={j.id} className="row-click" onClick={() => nav('run', { jobId: j.id })}>
                    <td><div className="row gap-2"><Dot status={status} /><span style={{ fontWeight: 500 }}>{plan.name}</span></div></td>
                    <td><Badge status={status} /></td>
                    <td><div className="row gap-3"><SegProgress pass={c.pass} fail={c.fail} running={c.running||0} total={c.total} className="grow" /><span className="mono t-xs faint" style={{ minWidth: 50 }}>{c.done}/{c.total}</span></div></td>
                    <td><span className="mono t-sm" style={{ color: pr>=0.7?'var(--pass)':'var(--warn)' }}>{(pr*100).toFixed(0)}%</span></td>
                    <td><span className="mono t-sm t2">{DB.avgScore(items).toFixed(2)}</span></td>
                    <td><span className="t-xs faint">{ago(j.startedAt)}</span></td>
                    <td><Icon name="chevR" size={14} style={{ color: 'var(--text-faint)' }} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ---------------- LIVE RUN MONITOR (hero) ---------------- */
function RunDetail({ jobId, nav, live, controls }) {
  const useLive = live && live.jobId === jobId;
  const staticJob = DB.getJob(jobId);
  const job = useLive ? live : staticJob;
  const [filter, setFilter] = useState('all');
  const [sel, setSel] = useState(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = job ? job.startedAt : Date.now();
    const t = setInterval(() => setElapsed(Date.now() - start), 1000);
    return () => clearInterval(t);
  }, [jobId]);

  if (!job) return <div className="page"><Empty icon="activity" title="Job not found" /></div>;
  const plan = DB.getPlan(job.planId); const bundle = DB.getBundle(job.bundleId);
  const items = job.items; const c = DB.counts(items); const pr = DB.passRate(items);
  const status = job.status;
  const running = status === 'running'; const paused = status === 'paused';
  const live2 = running || paused;
  const avgScore = DB.avgScore(items);
  const doneItems = items.filter(i => i.score != null);
  const avgLat = doneItems.length ? doneItems.reduce((s, i) => s + (i.durationMs||0), 0) / doneItems.length : 0;

  const filtered = items.filter(it => {
    if (filter === 'all') return true;
    if (filter === 'running') return it.status === 'running';
    return it.status === filter;
  });

  return (
    <div className="page">
      {/* header */}
      <div className="page-head" style={{ paddingBottom: 14 }}>
        <div className="row between">
          <div className="row gap-3">
            <span className="lnk t-sm" onClick={() => nav('runs')} style={{ paddingTop: 3 }}><Icon name="arrowLeft" size={15} /></span>
            <div className="col gap-1">
              <div className="row gap-2">
                <span className="t-h1" style={{ whiteSpace: 'nowrap' }}>{plan.name}</span>
                <Badge status={live2 ? (paused ? 'queued' : 'running') : status}>{paused ? 'paused' : undefined}</Badge>
              </div>
              <div className="cap mono" style={{ whiteSpace: 'nowrap' }}>{bundle.name} {bundle.version} · {job.maxWorkers} workers · {job.runsPerQuestion}× per question · job <span style={{ color: 'var(--text-muted)' }}>{job.id}</span></div>
            </div>
          </div>
          <div className="row gap-2">
            <Button size="sm" variant="ghost" icon="link" onClick={() => {}}>MLflow run</Button>
            {running && <><Button size="sm" icon="pause" onClick={controls.pause}>Pause</Button><Button size="sm" variant="danger" icon="x" onClick={controls.stop}>Stop</Button></>}
            {paused && <Button size="sm" variant="primary" icon="play" onClick={controls.resume}>Resume</Button>}
            {!live2 && <><Button size="sm" variant="ghost" icon="download">Export</Button><Button size="sm" variant="primary" icon="refresh" onClick={() => controls.rerun(job.planId)}>Re-run</Button></>}
          </div>
        </div>
      </div>

      <div className="page-body" style={{ padding: 0, display: 'flex', overflow: 'hidden' }}>
        {/* main column */}
        <div className="scroll-y" style={{ flex: 1, padding: '20px 24px 80px', minWidth: 0 }}>
          {/* KPIs */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', gap: 1, background: 'var(--border-soft)', border: '1px solid var(--border-soft)', borderRadius: 12, overflow: 'hidden', marginBottom: 18 }}>
            <BigStat label="Pass rate" value={`${(pr*100).toFixed(0)}%`} tone={pr>=0.7?'pass':'warn'} big />
            <BigStat label="Passed" value={c.pass} tone="pass" />
            <BigStat label="Failed" value={c.fail} tone="fail" />
            <BigStat label="Avg score" value={avgScore.toFixed(2)} />
            <BigStat label="Avg latency" value={dur(Math.round(avgLat))} />
            <BigStat label="Elapsed" value={fmtElapsed(elapsed)} mono />
          </div>

          {/* progress */}
          <div className="panel card-pad" style={{ marginBottom: 18 }}>
            <div className="row between" style={{ marginBottom: 10, flexWrap: 'nowrap' }}>
              <div className="row gap-2" style={{ whiteSpace: 'nowrap' }}>
                {live2 && <span className={`dot ${paused?'d-queued':'d-live'}`} />}
                <span className="t-sm" style={{ fontWeight: 500 }}>{c.done} of {c.total} tasks {live2 ? 'complete' : 'finished'}</span>
                {running && <span className="cap">· {c.running} running · {c.pending} queued</span>}
              </div>
              <span className="mono t-xs faint">{c.total - c.done} remaining</span>
            </div>
            <SegProgress pass={c.pass} fail={c.fail} running={c.running||0} total={c.total} className="" />
            <div className="row gap-4" style={{ marginTop: 12 }}>
              <Legend color="pass" label={`pass ${c.pass}`} />
              <Legend color="fail" label={`fail ${c.fail}`} />
              {(c.running>0) && <Legend color="run" label={`running ${c.running}`} />}
              <Legend color="surface" label={`queued ${c.pending}`} />
            </div>
          </div>

          {/* run items */}
          <div className="row between" style={{ marginBottom: 10 }}>
            <div className="row gap-2"><span className="t-h2" style={{ whiteSpace: 'nowrap' }}>Eval Run Items</span><span className="badge b-muted">{filtered.length}</span></div>
            <div className="seg-ctl">
              {['all','pass','fail','running'].map(f => (
                <button key={f} className={filter===f?'active':''} onClick={() => setFilter(f)}>{f[0].toUpperCase()+f.slice(1)}</button>
              ))}
            </div>
          </div>
          <div className="panel" style={{ overflow: 'hidden' }}>
            <table className="tbl">
              <thead><tr>
                <th style={{ width: 30 }}></th><th>Question</th><th style={{ width: 56 }}>Run</th><th style={{ width: 88 }}>Status</th><th style={{ width: 64 }}>Score</th><th style={{ width: 70 }}>Latency</th><th>Flags</th>
              </tr></thead>
              <tbody>
                {filtered.slice(0, 80).map(it => (
                  <RunItemRow key={it.id} it={it} onClick={() => it.status !== 'pending' && it.status !== 'running' && setSel(it)} />
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && <div style={{ padding: 30 }}><Empty icon="filter" title="Nothing here yet" sub="No run items match this filter." /></div>}
          </div>
        </div>

        {/* right rail */}
        <div className="scroll-y" style={{ width: 296, flex: 'none', borderLeft: '1px solid var(--border-soft)', background: 'var(--bg-deep)', padding: 18 }}>
          <WorkerPanel job={job} live={live2 && !paused} />
          <div style={{ height: 18 }} />
          <ByQuestion items={items} plan={plan} />
          <div style={{ height: 18 }} />
          <MlflowCard job={job} />
        </div>
      </div>

      {sel && <RunItemDrawer it={sel} plan={plan} bundle={bundle} onClose={() => setSel(null)} />}
    </div>
  );
}

function fmtElapsed(ms) {
  const s = Math.floor(ms/1000); const m = Math.floor(s/60);
  return `${m}:${String(s%60).padStart(2,'0')}`;
}
function BigStat({ label, value, tone, big, mono }) {
  return (
    <div className="col gap-1" style={{ padding: '13px 15px', background: 'var(--panel)' }}>
      <span className="t-label" style={{ fontSize: 9.5 }}>{label}</span>
      <span className="stat-val" style={{ fontSize: big ? 26 : 19, color: tone ? `var(--${tone})` : 'var(--text)' }}>{value}</span>
    </div>
  );
}
function Legend({ color, label }) {
  return <span className="row gap-2 cap"><span style={{ width: 8, height: 8, borderRadius: 2, background: `var(--${color})` }} />{label}</span>;
}

function RunItemRow({ it, onClick }) {
  const flash = it._flash && Date.now() - it._flash < 1100;
  const cls = flash ? (it.status === 'pass' ? 'flash-pass' : 'flash-fail') : '';
  const clickable = it.status === 'pass' || it.status === 'fail';
  return (
    <tr className={`${cls} ${clickable?'row-click':''}`} onClick={onClick} style={{ cursor: clickable?'pointer':'default' }}>
      <td><Dot status={it.status} /></td>
      <td style={{ maxWidth: 0 }}><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.input}</div></td>
      <td><span className="mono t-xs faint">#{it.attempt}</span></td>
      <td>{it.status === 'pending' ? <span className="cap mono">queued</span> : it.status === 'running' ? <span className="row gap-2 mono t-xs" style={{ color: 'var(--run)' }}><Spinner size={11} /><span style={{ marginLeft: 14 }}>running</span></span> : <Badge status={it.status} />}</td>
      <td>{it.score != null ? <span className="mono t-sm" style={{ color: it.status==='pass'?'var(--pass)':'var(--fail)' }}>{it.score.toFixed(2)}</span> : <span className="faint">—</span>}</td>
      <td><span className="mono t-xs faint">{dur(it.durationMs)}</span></td>
      <td>{it.flags && it.flags.length ? <div className="row gap-1 wrap">{it.flags.slice(0,2).map(f => <span key={f} className="badge b-fail mono" style={{ fontSize: 10, height: 18 }}>{f}</span>)}</div> : <span className="faint">—</span>}</td>
    </tr>
  );
}

function WorkerPanel({ job, live }) {
  const running = job.items.filter(i => i.status === 'running');
  const slots = Array.from({ length: job.maxWorkers });
  return (
    <div>
      <div className="row between" style={{ marginBottom: 10 }}>
        <div className="t-label">Workers</div>
        <span className="mono t-xs" style={{ color: live?'var(--run)':'var(--text-faint)' }}>{running.length}/{job.maxWorkers} active</span>
      </div>
      <div className="col gap-2">
        {slots.map((_, i) => {
          const item = running[i];
          return (
            <div key={i} className="row gap-2" style={{ padding: '7px 9px', borderRadius: 6, background: 'var(--panel)', border: '1px solid var(--border-soft)' }}>
              <span className="mono t-xs faint" style={{ width: 18 }}>w{i+1}</span>
              {item ? (
                <>
                  <span className="dot d-run" />
                  <span className="t-xs t2" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.input}</span>
                </>
              ) : (
                <><span className="dot d-draft" /><span className="cap" style={{ flex: 1 }}>{live ? 'waiting for task' : 'idle'}</span></>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ByQuestion({ items, plan }) {
  const byQ = plan.questions.map((q, qi) => {
    const its = items.filter(i => i.qIndex === qi);
    const pass = its.filter(i => i.status === 'pass').length;
    const fail = its.filter(i => i.status === 'fail').length;
    return { q, qi, pass, fail, total: its.length, done: pass + fail };
  });
  return (
    <div>
      <div className="t-label" style={{ marginBottom: 10 }}>Pass / fail by question</div>
      <div className="col gap-2">
        {byQ.map(({ q, qi, pass, fail, total, done }) => (
          <div key={qi} className="col gap-1">
            <div className="row between">
              <span className="t-xs t2" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 200 }}>{qi+1}. {q.input}</span>
              <span className="mono t-xs faint">{pass}/{done||0}</span>
            </div>
            <div className="prog seg" style={{ height: 5 }}>
              <i style={{ width: `${pass/total*100}%`, background: 'var(--pass)' }} />
              <i style={{ width: `${fail/total*100}%`, background: 'var(--fail)' }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MlflowCard({ job }) {
  return (
    <div className="panel card-pad col gap-3">
      <div className="row between"><span className="t-label">MLflow tracking</span><Icon name="ext" size={13} style={{ color: 'var(--text-faint)' }} /></div>
      <div className="col gap-2">
        <div className="row between"><span className="cap">Parent run</span><span className="mono t-xs t2">{job.mlflowParent}</span></div>
        <div className="row between"><span className="cap">Traces linked</span><span className="mono t-xs t2">{job.items.filter(i=>i.score!=null).length}</span></div>
        <div className="row between"><span className="cap">Experiment</span><span className="mono t-xs t2">dspy-trainer</span></div>
      </div>
      <Button size="sm" variant="outline" iconRight="ext" className="btn-block">Open in MLflow</Button>
    </div>
  );
}

/* ---------------- Run item drawer ---------------- */
function RunItemDrawer({ it, plan, bundle, onClose }) {
  const pass = it.status === 'pass';
  const pred = it.prediction || {};
  return (
    <Drawer onClose={onClose}>
      <div className="row between" style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-soft)', flex: 'none' }}>
        <div className="row gap-3">
          <Badge status={it.status} />
          <span className="mono t-sm t2">item {it.id.split('_').slice(-2).join('·')}</span>
        </div>
        <Button variant="ghost" size="sm" icon="x" onClick={onClose} />
      </div>
      <div className="scroll-y" style={{ padding: 20, flex: 1 }}>
        {/* judge result hero */}
        <div className="panel card-pad" style={{ marginBottom: 18, background: pass?'var(--pass-dim)':'var(--fail-dim)', border: 'none' }}>
          <div className="row between" style={{ marginBottom: 10 }}>
            <span className="t-label" style={{ color: pass?'var(--pass)':'var(--fail)' }}>Judge result</span>
            <div className="row gap-4">
              <div className="col" style={{ alignItems: 'flex-end' }}><span className="cap">score</span><span className="stat-val" style={{ fontSize: 18, color: pass?'var(--pass)':'var(--fail)' }}>{it.score?.toFixed(2)}</span></div>
              <div className="col" style={{ alignItems: 'flex-end' }}><span className="cap">verdict</span><span className="mono t-sm" style={{ fontWeight: 600, color: pass?'var(--pass)':'var(--fail)' }}>{pass?'PASS':'FAIL'}</span></div>
            </div>
          </div>
          <div className="t-sm t2" style={{ lineHeight: 1.55 }}>{it.rationale}</div>
          {it.flags && it.flags.length > 0 && (
            <div className="row gap-2 wrap" style={{ marginTop: 12 }}>
              {it.flags.map(f => <span key={f} className="badge b-fail mono"><Icon name="flag" size={11} />{f}</span>)}
            </div>
          )}
        </div>

        <DrawerSection label="Input prompt">
          <div className="code" style={{ whiteSpace: 'pre-wrap' }}>{it.input}</div>
        </DrawerSection>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 18 }}>
          <div className="col gap-2">
            <div className="t-label">Label payload <span className="faint">(gold)</span></div>
            <div className="code" style={{ whiteSpace: 'pre-wrap', minHeight: 70 }}>{it.expected}</div>
          </div>
          <div className="col gap-2">
            <div className="t-label">Prediction payload</div>
            <div className="code" style={{ whiteSpace: 'pre-wrap', minHeight: 70 }}>
              <span className="c-key">category</span>: {pred.category}{'\n'}<span className="c-key">priority</span>: {pred.priority}{'\n'}<span className="c-key">reply</span>: {pred.reply}
            </div>
          </div>
        </div>

        <DrawerSection label="Judge raw_response">
          <pre className="code">{`{`}{'\n'}{`  `}<span className="c-key">"score"</span>: <span className="c-num">{it.score?.toFixed(2)}</span>,{'\n'}{`  `}<span className="c-key">"passed"</span>: <span className="c-str">{String(pass)}</span>,{'\n'}{`  `}<span className="c-key">"flags"</span>: [{(it.flags||[]).map(f=>`"${f}"`).join(', ')}],{'\n'}{`  `}<span className="c-key">"category_match"</span>: <span className="c-str">{String(pass)}</span>{'\n'}{`}`}</pre>
        </DrawerSection>

        <div className="row between" style={{ padding: '13px 0', borderTop: '1px solid var(--border-soft)' }}>
          <div className="row gap-5">
            <MetaItem2 label="Latency" value={dur(it.durationMs)} />
            <MetaItem2 label="Attempt" value={`#${it.attempt}`} />
            <MetaItem2 label="MLflow trace" value={it.traceId} />
          </div>
          <Button size="sm" variant="outline" iconRight="ext">Trace</Button>
        </div>
      </div>
    </Drawer>
  );
}
function DrawerSection({ label, children }) {
  return <div className="col gap-2" style={{ marginBottom: 18 }}><div className="t-label">{label}</div>{children}</div>;
}
function MetaItem2({ label, value }) {
  return <div className="col gap-1"><span className="t-label" style={{ fontSize: 9 }}>{label}</span><span className="mono t-xs t2">{value}</span></div>;
}

Object.assign(window, { RunsScreen });
