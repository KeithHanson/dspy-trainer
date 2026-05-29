/* ============================================================
   Team members + invites
   ============================================================ */
function TeamScreen({ nav, toast }) {
  const [members, setMembers] = useState(DB.team);
  const [showInvite, setShowInvite] = useState(false);

  const active = members.filter(m => m.status === 'active');
  const pending = members.filter(m => m.status === 'invited');

  return (
    <div className="page">
      <div className="page-body" style={{ maxWidth: 880 }}>
        <div className="row between" style={{ marginBottom: 22 }}>
          <div className="col gap-1">
            <div className="t-display" style={{ fontSize: 22 }}>Team</div>
            <div className="muted t-sm">{active.length} members · {pending.length} pending · {DB.ORG.plan} plan</div>
          </div>
          <Button variant="primary" icon="plus" onClick={() => setShowInvite(true)}>Invite members</Button>
        </div>

        {/* seats */}
        <div className="panel card-pad row between" style={{ marginBottom: 18 }}>
          <div className="row gap-3">
            <Icon name="users" size={18} style={{ color: 'var(--accent)' }} />
            <div className="col" style={{ lineHeight: 1.3 }}>
              <span className="t-sm" style={{ fontWeight: 500 }}>{active.length} of 10 seats used</span>
              <span className="cap">Members can run plans and view results. Admins manage bundles + billing.</span>
            </div>
          </div>
          <div style={{ width: 160 }}><Progress value={active.length/10} /></div>
        </div>

        {/* members table */}
        <div className="panel" style={{ overflow: 'hidden' }}>
          <table className="tbl">
            <thead><tr><th>Member</th><th>Role</th><th>Status</th><th>Last active</th><th></th></tr></thead>
            <tbody>
              {members.map(m => (
                <tr key={m.id}>
                  <td>
                    <div className="row gap-3">
                      {m.status==='invited'
                        ? <div className="center" style={{ width: 26, height: 26, borderRadius: 999, border: '1px dashed var(--border-strong)', color: 'var(--text-faint)' }}><Icon name="mail" size={13} /></div>
                        : <Avatar initials={m.initials} id={m.id} />}
                      <div className="col" style={{ lineHeight: 1.3 }}>
                        <span className="t-sm" style={{ fontWeight: 500 }}>{m.status==='invited' ? <span className="faint">Invitation pending</span> : m.name}{m.id===DB.USER.id && <span className="muted" style={{ fontWeight: 400 }}> (you)</span>}</span>
                        <span className="cap mono">{m.email}</span>
                      </div>
                    </div>
                  </td>
                  <td>
                    {m.role === 'Owner'
                      ? <span className="badge b-accent">{m.role}</span>
                      : <span className="row gap-1 mono t-xs muted">{m.role}<Icon name="chevD" size={11} /></span>}
                  </td>
                  <td>{m.status==='active' ? <span className="row gap-2"><span className="dot d-pass" /><span className="t-xs t2 row gap-1">via {m.via}{m.via==='github' && <Icon name="github" size={12} />}{m.via==='google' && <Icon name="google" size={12} />}</span></span> : <Badge status="invited" />}</td>
                  <td><span className="t-xs faint">{m.last ? ago(m.last) : '—'}</span></td>
                  <td>
                    {m.status==='invited'
                      ? <Button size="sm" variant="ghost">Resend</Button>
                      : m.id!==DB.USER.id && <Button size="sm" variant="ghost" icon="dots" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {showInvite && <InviteModal onClose={() => setShowInvite(false)} toast={toast} />}
    </div>
  );
}

function randToken() { return Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 6); }

function InviteModal({ onClose, toast }) {
  const [role, setRole] = useState('Member');
  const [token, setToken] = useState(randToken);
  const [copied, setCopied] = useState(false);
  const link = `https://dspy-trainer.app/join/${DB.ORG.slug}#${token}`;

  const copyLink = () => {
    try { navigator.clipboard && navigator.clipboard.writeText(link); } catch (e) {}
    setCopied(true); setTimeout(() => setCopied(false), 1800);
    toast && toast({ title: 'Invite link copied', sub: `Joins as ${role} · expires in 7 days`, icon: 'link' });
  };
  const resetLink = () => { setToken(randToken()); toast && toast({ title: 'Invite link reset', sub: 'The previous link no longer works', icon: 'refresh', tone: 'warn' }); };

  return (
    <Modal title="Invite team members" onClose={onClose}
      footer={<>
        <span className="cap row gap-2"><span className="dot d-pass" />Link active · expires in 7 days</span>
        <Button variant="primary" icon={copied ? 'check' : 'copy'} onClick={copyLink}>{copied ? 'Copied' : 'Copy invite link'}</Button>
      </>}>
      <div className="col gap-4">
        <div className="col gap-2">
          <label className="field-label">Shareable invite link</label>
          <div className="row gap-2">
            <div className="input mono row" style={{ alignItems: 'center', overflow: 'hidden', cursor: 'default' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-2)' }}>{link}</span>
            </div>
            <Button icon={copied ? 'check' : 'copy'} onClick={copyLink} style={{ flex: 'none' }}>{copied ? 'Copied' : 'Copy'}</Button>
          </div>
          <div className="row between" style={{ marginTop: 2 }}>
            <span className="field-hint" style={{ marginTop: 0 }}>Anyone with this link can join <strong style={{ color: 'var(--text-2)', fontWeight: 500 }}>{DB.ORG.name}</strong> as a <strong style={{ color: 'var(--text-2)', fontWeight: 500 }}>{role}</strong>.</span>
            <Button variant="ghost" size="sm" icon="refresh" onClick={resetLink} style={{ flex: 'none' }}>Reset</Button>
          </div>
        </div>

        <div>
          <label className="field-label">Role granted by this link</label>
          <div className="row gap-2">
            {[['Member', 'Run plans, view results'], ['Admin', 'Manage bundles & team'], ['Owner', 'Full access + billing']].map(([r, d]) => (
              <button key={r} className="col gap-1" onClick={() => setRole(r)} style={{
                flex: 1, padding: '10px 12px', textAlign: 'left', borderRadius: 8,
                border: `1px solid ${role === r ? 'var(--accent-line)' : 'var(--border)'}`,
                background: role === r ? 'var(--accent-dim)' : 'var(--bg-deep)' }}>
                <span className="t-sm" style={{ fontWeight: 500 }}>{r}</span>
                <span className="cap">{d}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

Object.assign(window, { TeamScreen });
