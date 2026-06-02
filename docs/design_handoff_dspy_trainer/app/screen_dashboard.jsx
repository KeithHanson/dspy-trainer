/* ============================================================
   Dashboard / Overview
   ============================================================ */
function Dashboard({ nav, liveJob }) {
  const running = liveJob || DB.jobs.find(j => j.status === 'running');
  const recentJobs = DB.jobs.slice().sort((a, b) => b.startedAt - a.startedAt);

  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 1100 }}>
        {/* greeting */}
        <div className="row between" style={{ marginBottom: 22 }}>
          <div className="col gap-1">
            <div className="t-display">Good morning, Kira</div>
            <div className="muted t-sm">3 plans active · {DB.bundles.filter(b=>b.status==='valid').length} validated bundles · last run 2m ago</div>
          </div>
          <div className="row gap-2">
            <Button icon="upload" onClick={() => nav('bundle', { upload: true })}>Upload bundle</Button>
            <Button variant="primary" icon="plus" onClick={() => nav('plan-new')}>New plan</Button>
          </div>
        </div>

        {/* live job hero */}
        {running && <LiveStrip job={running} nav={nav} />}

        {/* stats */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginTop: 18 }}>
          <KpiCard icon="gauge" label="Pass rate · 7d" value="82.4%" delta="+3.1pt" tone="pass" spark={[60,64,58,70,66,74,72,78,80,82]} />
          <KpiCard icon="activity" label="Eval jobs · 7d" value="41" delta="+12" tone="run" spark={[3,5,4,8,6,9,7,11,8,12]} />
          <KpiCard icon="layers" label="Tasks judged" value="6,284" delta="+1.4k" tone="info" spark={[200,340,280,520,460,610,540,720,680,840]} />
          <KpiCard icon="clock" label="Avg item latency" value="2.8s" delta="-0.4s" tone="warn" spark={[40,38,42,36,34,33,31,30,29,28]} />
        </div>

        {/* recent jobs */}
        <div className="row between" style={{ margin: '30px 0 12px' }}>
          <div className="t-h2">Recent runs</div>
          <span className="lnk t-sm" onClick={() => nav('runs')}>View all <Icon name="arrowRight" size={13} style={{ verticalAlign: -2 }} /></span>
        </div>
        <div className="panel" style={{ overflow: 'hidden' }}>
          <table className="tbl">
            <thead><tr>
              <th>Plan</th><th>Bundle</th><th>Status</th><th style={{ width: 200 }}>Progress</th><th>Pass</th><th>Started</th><th></th>
            </tr></thead>
            <tbody>
              {recentJobs.map(j => {
                const plan = DB.getPlan(j.planId); const bundle = DB.getBundle(j.bundleId);
                const c = DB.counts(j.items); const pr = DB.passRate(j.items);
                return (
                  <tr key={j.id} className="row-click" onClick={() => nav('run', { jobId: j.id })}>
                    <td><div className="row gap-2"><Dot status={j.status} /><span style={{ fontWeight: 500 }}>{plan.name}</span></div></td>
                    <td><span className="mono t-xs muted">{bundle.name} <span style={{ color: 'var(--accent)' }}>{bundle.version}</span></span></td>
                    <td><Badge status={j.status} /></td>
                    <td>
                      <div className="row gap-3">
                        <SegProgress pass={c.pass} fail={c.fail} running={c.running||0} total={c.total} className="grow" />
                        <span className="mono t-xs faint" style={{ minWidth: 52 }}>{c.done}/{c.total}</span>
                      </div>
                    </td>
                    <td><span className="mono t-sm" style={{ color: pr >= 0.7 ? 'var(--pass)' : 'var(--warn)' }}>{(pr*100).toFixed(0)}%</span></td>
                    <td><span className="t-xs faint">{ago(j.startedAt)}</span></td>
                    <td><Icon name="chevR" size={14} style={{ color: 'var(--text-faint)' }} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* two-up: needs attention + quick start */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 18, marginTop: 24 }}>
          <div className="panel card-pad">
            <div className="row between" style={{ marginBottom: 14 }}>
              <div className="t-h2">Needs attention</div>
              <Badge status="fail" icon="alert">1 bundle</Badge>
            </div>
            <div className="row gap-3" style={{ padding: 13, borderRadius: 8, background: 'var(--fail-dim)', border: '1px solid transparent' }}>
              <Icon name="alert" size={17} style={{ color: 'var(--fail)', marginTop: 1, flex: 'none' }} />
              <div className="col gap-1" style={{ flex: 1 }}>
                <div className="t-sm" style={{ fontWeight: 500 }}>sql-generator <span className="mono faint" style={{ fontWeight: 400 }}>v7</span> failed validation</div>
                <div className="cap">metric.py is missing the <span className="mono">trace</span> parameter · dspy version pin conflict</div>
              </div>
              <Button size="sm" onClick={() => nav('bundle', { id: 'bdl_a90e' })}>Fix</Button>
            </div>
            <div className="row gap-3" style={{ padding: 13, marginTop: 10, borderRadius: 8, background: 'var(--warn-dim)' }}>
              <Icon name="info" size={17} style={{ color: 'var(--warn)', marginTop: 1, flex: 'none' }} />
              <div className="col gap-1" style={{ flex: 1 }}>
                <div className="t-sm" style={{ fontWeight: 500 }}>1 draft plan ready to run</div>
                <div className="cap">Triage — refund edge cases · 5 questions × 3 runs</div>
              </div>
              <Button size="sm" onClick={() => nav('plans')}>Review</Button>
            </div>
          </div>

          <div className="panel card-pad col gap-3">
            <div className="t-h2">Quick start</div>
            {[
              ['download', 'Download example bundle', 'A working triage agent + metric', () => nav('bundle', { upload: true })],
              ['box', 'Upload your module', 'module.py + metric.py, zipped', () => nav('bundle', { upload: true })],
              ['users', 'Invite your team', 'Share results with reviewers', () => nav('team')],
            ].map(([ic, t, s, fn]) => (
              <button key={t} className="row gap-3" style={{ textAlign: 'left', padding: '4px 0' }} onClick={fn}>
                <div className="center" style={{ width: 34, height: 34, borderRadius: 8, background: 'var(--panel-2)', border: '1px solid var(--border-soft)', color: 'var(--accent)', flex: 'none' }}><Icon name={ic} size={16} /></div>
                <div className="col" style={{ flex: 1, lineHeight: 1.3 }}>
                  <span className="t-sm" style={{ fontWeight: 500 }}>{t}</span>
                  <span className="cap">{s}</span>
                </div>
                <Icon name="arrowRight" size={14} style={{ color: 'var(--text-faint)' }} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function KpiCard({ icon, label, value, delta, tone, spark }) {
  const max = Math.max(...spark);
  const up = delta && delta.startsWith('+');
  return (
    <div className="panel card-pad col gap-3">
      <div className="row between">
        <span className="t-label" style={{ fontSize: 10 }}>{label}</span>
        <Icon name={icon} size={14} style={{ color: 'var(--text-faint)' }} />
      </div>
      <div className="row gap-2" style={{ alignItems: 'baseline' }}>
        <span className="stat-val" style={{ fontSize: 24 }}>{value}</span>
        <span className="mono t-xs" style={{ color: tone === 'warn' ? 'var(--warn)' : 'var(--pass)' }}>{delta}</span>
      </div>
      <div className="bars">
        {spark.map((v, i) => <i key={i} style={{ height: `${(v/max)*100}%`, background: i === spark.length-1 ? `var(--${tone})` : undefined }} />)}
      </div>
    </div>
  );
}

function LiveStrip({ job, nav }) {
  const jobId = job.id || job.jobId;
  const c = DB.counts(job.items); const pr = DB.passRate(job.items);
  const plan = DB.getPlan(job.planId); const bundle = DB.getBundle(job.bundleId);
  return (
    <div className="card" style={{ padding: 18, background: 'linear-gradient(180deg, var(--panel), var(--bg-deep))', cursor: 'pointer', borderColor: 'var(--accent-line)' }}
      onClick={() => nav('run', { jobId })}>
      <div className="row between" style={{ marginBottom: 14 }}>
        <div className="row gap-3">
          <span className="dot d-live" />
          <div className="col" style={{ lineHeight: 1.3 }}>
            <div className="t-h2">{plan.name}</div>
            <div className="cap mono">{bundle.name} {bundle.version} · {job.maxWorkers} workers · {job.runsPerQuestion}× per question</div>
          </div>
        </div>
        <Button variant="primary" size="sm" iconRight="arrowRight">Open live monitor</Button>
      </div>
      <div className="row gap-4">
        <div className="grow col gap-2">
          <div className="row between">
            <span className="cap">{c.done} of {c.total} tasks complete</span>
            <span className="mono t-xs" style={{ color: 'var(--pass)' }}>{(pr*100).toFixed(0)}% pass</span>
          </div>
          <SegProgress pass={c.pass} fail={c.fail} running={c.running||0} total={c.total} />
        </div>
        <div className="vr" />
        <div className="row gap-5">
          <Stat label="pass" val={c.pass} tone="pass" />
          <Stat label="fail" val={c.fail} tone="fail" />
          <Stat label="running" val={c.running || 0} tone="run" />
          <Stat label="queued" val={c.pending} tone="" />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Dashboard });
