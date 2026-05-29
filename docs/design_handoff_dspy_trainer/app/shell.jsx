/* ============================================================
   App shell: sidebar + topbar + nav
   ============================================================ */
const NAV = [
  { key: 'dashboard', label: 'Overview', icon: 'grid' },
  { key: 'bundles', label: 'Module Bundles', icon: 'box' },
  { key: 'plans', label: 'Evaluation Plans', icon: 'layers' },
  { key: 'runs', label: 'Eval Jobs', icon: 'activity' },
];
const NAV2 = [
  { key: 'team', label: 'Team', icon: 'users' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
];

function Sidebar({ route, nav, liveJob }) {
  const item = (it) => {
    const active = route.name === it.key || (it.key === 'bundles' && route.name === 'bundle') ||
      (it.key === 'plans' && route.name === 'plan-new') || (it.key === 'runs' && route.name === 'run');
    const showLive = it.key === 'runs' && liveJob;
    return (
      <button key={it.key} onClick={() => nav(it.key)}
        className="row gap-3" style={{
          padding: '7px 10px', borderRadius: 6, width: '100%', textAlign: 'left',
          color: active ? 'var(--text)' : 'var(--text-muted)',
          background: active ? 'var(--surface)' : 'transparent',
          fontSize: 13.5, fontWeight: active ? 500 : 400, transition: 'background .12s, color .12s',
        }}
        onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--panel-2)'; }}
        onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent'; }}>
        <Icon name={it.icon} size={16} style={{ color: active ? 'var(--accent)' : 'var(--text-faint)' }} />
        <span style={{ flex: 1 }}>{it.label}</span>
        {showLive && <span className="dot d-live" />}
      </button>
    );
  };
  return (
    <aside className="col" style={{ width: 'var(--sidebar-w)', flex: 'none', background: 'var(--bg-deep)', borderRight: '1px solid var(--border-soft)' }}>
      {/* org switcher */}
      <div className="row gap-3" style={{ height: 'var(--topbar-h)', padding: '0 14px', borderBottom: '1px solid var(--border-soft)' }}>
        <div className="center" style={{ width: 26, height: 26, borderRadius: 7, background: 'var(--accent)', color: 'var(--accent-ink)', flex: 'none' }}>
          <Icon name="bolt" size={15} strokeWidth={2.2} />
        </div>
        <div className="col" style={{ flex: 1, lineHeight: 1.2 }}>
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em' }}>{DB.ORG.name}</div>
          <div className="t-label" style={{ fontSize: 10 }}>dspy-trainer</div>
        </div>
        <Icon name="chevD" size={13} style={{ color: 'var(--text-faint)' }} />
      </div>

      <div className="col" style={{ padding: '12px 10px', gap: 2, flex: 1 }}>
        {NAV.map(item)}
        <div style={{ height: 1, background: 'var(--border-soft)', margin: '10px 8px' }} />
        {NAV2.map(item)}
      </div>

      {/* user */}
      <div className="row gap-3" style={{ padding: '10px 12px', borderTop: '1px solid var(--border-soft)' }}>
        <Avatar initials={DB.USER.initials} id={DB.USER.id} size={28} />
        <div className="col" style={{ flex: 1, lineHeight: 1.25, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{DB.USER.name}</div>
          <div className="cap" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{DB.USER.email}</div>
        </div>
        <Button variant="ghost" size="sm" icon="logout" onClick={() => nav('__logout')} title="Sign out" />
      </div>
    </aside>
  );
}

function Topbar({ crumbs, actions }) {
  return (
    <header className="row between" style={{ height: 'var(--topbar-h)', flex: 'none', padding: '0 18px', borderBottom: '1px solid var(--border-soft)', background: 'var(--bg)' }}>
      <div className="row gap-2" style={{ fontSize: 13 }}>
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            {i > 0 && <Icon name="chevR" size={13} style={{ color: 'var(--text-faint)' }} />}
            <span style={{ color: i === crumbs.length - 1 ? 'var(--text)' : 'var(--text-muted)',
              fontWeight: i === crumbs.length - 1 ? 500 : 400, cursor: c.onClick ? 'pointer' : 'default' }}
              onClick={c.onClick}>{c.label}</span>
          </React.Fragment>
        ))}
      </div>
      <div className="row gap-2">
        <button className="row gap-2" style={{ height: 30, padding: '0 10px 0 9px', border: '1px solid var(--border-soft)', borderRadius: 6, color: 'var(--text-faint)', background: 'var(--bg-deep)', fontSize: 12.5 }}>
          <Icon name="search" size={14} /><span>Search</span>
          <span className="kbd" style={{ marginLeft: 6 }}>⌘K</span>
        </button>
        {actions}
      </div>
    </header>
  );
}

function AppShell({ route, nav, crumbs, actions, children, liveJob }) {
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', height: '100%' }}>
      <Sidebar route={route} nav={nav} liveJob={liveJob} />
      <div className="col" style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
        <Topbar crumbs={crumbs} actions={actions} />
        <div style={{ flex: 1, minHeight: 0 }}>{children}</div>
      </div>
    </div>
  );
}

Object.assign(window, { Sidebar, Topbar, AppShell, NAV });
