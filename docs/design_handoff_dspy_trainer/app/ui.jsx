/* ============================================================
   UI primitives + icon set  →  window globals
   ============================================================ */
const { useState, useEffect, useRef, useCallback, createContext, useContext } = React;

/* ---------- Icons (simple feather-style strokes) ---------- */
const ICONS = {
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM13 13h7v7h-7zM4 13h7v7H4z',
  box: 'M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8M12 13v8',
  layers: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17l9 5 9-5',
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13A4 4 0 0 1 16 11',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  plus: 'M12 5v14M5 12h14',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  check: 'M20 6L9 17l-5-5',
  x: 'M18 6L6 18M6 6l12 12',
  alert: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01',
  play: 'M5 3l14 9-14 9V3z',
  pause: 'M6 4h4v16H6zM14 4h4v16h-4z',
  arrowRight: 'M5 12h14M12 5l7 7-7 7',
  arrowLeft: 'M19 12H5M12 19l-7-7 7-7',
  chevR: 'M9 18l6-6-6-6',
  chevD: 'M6 9l6 6 6-6',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  zap: 'M13 2L3 14h9l-1 8 10-12h-9l1-8z',
  cpu: 'M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2M4 4h16v16H4zM9 9h6v6H9z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6',
  copy: 'M9 9h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  ext: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3',
  trash: 'M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2',
  github: 'M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22',
  google: 'GOOGLE',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6',
  bolt: 'M13 2L3 14h9l-1 8 10-12h-9l1-8z',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15',
  dots: 'M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  filter: 'M22 3H2l8 9.46V19l4 2v-8.54L22 3z',
  flag: 'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7',
  gauge: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 12l4-4',
  terminal: 'M4 17l6-6-6-6M12 19h8',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  link: 'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
  doc: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
};

function Icon({ name, size = 16, style, className, strokeWidth = 1.75 }) {
  if (name === 'google') {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" style={style} className={className}>
        <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z"/>
        <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"/>
        <path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"/>
        <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"/>
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" style={style} className={className}>
      <path d={ICONS[name] || ICONS.box} />
    </svg>
  );
}

/* ---------- Button ---------- */
function Button({ variant = 'default', size, icon, iconRight, children, className = '', ...rest }) {
  const cls = ['btn',
    variant === 'primary' && 'btn-primary',
    variant === 'ghost' && 'btn-ghost',
    variant === 'outline' && 'btn-outline',
    variant === 'danger' && 'btn-danger',
    size === 'sm' && 'btn-sm',
    size === 'lg' && 'btn-lg',
    !children && 'btn-icon',
    className].filter(Boolean).join(' ');
  return (
    <button className={cls} {...rest}>
      {icon && <Icon name={icon} />}
      {children}
      {iconRight && <Icon name={iconRight} />}
    </button>
  );
}

/* ---------- Badge / Status ---------- */
const STATUS_MAP = {
  pass: ['b-pass', 'd-pass', 'pass'], fail: ['b-fail', 'd-fail', 'fail'],
  succeeded: ['b-pass', 'd-pass', 'succeeded'], failed: ['b-fail', 'd-fail', 'failed'],
  running: ['b-run', 'd-run', 'running'], queued: ['b-warn', 'd-queued', 'queued'],
  draft: ['b-muted', 'd-draft', 'draft'], pending: ['b-muted', 'd-draft', 'pending'],
  valid: ['b-pass', 'd-pass', 'valid'], invalid: ['b-fail', 'd-fail', 'invalid'],
  validating: ['b-info', 'd-run', 'validating'], active: ['b-pass', 'd-pass', 'active'],
  invited: ['b-warn', 'd-queued', 'invited'], live: ['b-accent', 'd-live', 'live'],
};
function Badge({ status, children, icon, className = '' }) {
  const m = STATUS_MAP[status];
  const cls = m ? m[0] : '';
  return <span className={`badge ${cls} ${className}`}>
    {status && <span className={`dot ${m ? m[1] : ''}`} style={{ width: 6, height: 6 }} />}
    {icon && <Icon name={icon} />}
    {children || (m ? m[2] : status)}
  </span>;
}
function Dot({ status, glow }) {
  const m = STATUS_MAP[status];
  return <span className={`dot ${m ? m[1] : ''}`} />;
}

/* ---------- Progress ---------- */
function Progress({ value, className = '' }) {
  return <div className={`prog ${className}`}><i style={{ width: `${Math.round(value * 100)}%` }} /></div>;
}
function SegProgress({ pass, fail, running, total, className = '' }) {
  const p = (n) => total ? `${(n / total) * 100}%` : '0%';
  return (
    <div className={`prog seg ${className}`}>
      <i style={{ width: p(pass), background: 'var(--pass)' }} />
      <i style={{ width: p(fail), background: 'var(--fail)' }} />
      <i style={{ width: p(running), background: 'var(--run)' }} />
    </div>
  );
}

/* ---------- Avatar ---------- */
const AV_HUES = [156, 245, 25, 300, 78, 200];
function Avatar({ initials, id = '', size = 26 }) {
  const hue = AV_HUES[(id.charCodeAt(id.length - 1) || 0) % AV_HUES.length];
  return <div className="avatar" style={{ width: size, height: size, fontSize: size * 0.42,
    background: `oklch(0.72 0.13 ${hue})` }}>{initials}</div>;
}

/* ---------- Modal ---------- */
function Modal({ title, children, onClose, footer, width }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return ReactDOM.createPortal(
    <React.Fragment>
      <div className="scrim" onClick={onClose} />
      <div className="modal" style={width ? { width } : null}>
        <div className="row between" style={{ padding: '16px 18px', borderBottom: '1px solid var(--border-soft)' }}>
          <div className="t-h2">{title}</div>
          <Button variant="ghost" size="sm" icon="x" onClick={onClose} />
        </div>
        <div style={{ padding: '18px' }}>{children}</div>
        {footer && <div className="row between gap-3" style={{ padding: '14px 18px', borderTop: '1px solid var(--border-soft)' }}>{footer}</div>}
      </div>
    </React.Fragment>, document.body);
}

/* ---------- Drawer ---------- */
function Drawer({ children, onClose, width }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return ReactDOM.createPortal(
    <React.Fragment>
      <div className="scrim" onClick={onClose} />
      <div className="drawer" style={width ? { width } : null}>{children}</div>
    </React.Fragment>, document.body);
}

/* ---------- Empty state ---------- */
function Empty({ icon, title, sub, action }) {
  return (
    <div className="col center" style={{ padding: '64px 20px', textAlign: 'center', gap: 14 }}>
      <div className="center" style={{ width: 46, height: 46, borderRadius: 12, background: 'var(--panel-2)', border: '1px solid var(--border-soft)', color: 'var(--text-faint)' }}>
        <Icon name={icon} size={20} />
      </div>
      <div className="col gap-1" style={{ alignItems: 'center' }}>
        <div className="t-h2">{title}</div>
        {sub && <div className="muted t-sm" style={{ maxWidth: 320 }}>{sub}</div>}
      </div>
      {action}
    </div>
  );
}

/* ---------- relative time ---------- */
function ago(ts) {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return s + 's ago';
  const m = Math.floor(s / 60); if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60); if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}
function dur(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return ms + 'ms';
  return (ms / 1000).toFixed(1) + 's';
}

/* ---------- Toast context ---------- */
const ToastCtx = createContext(null);
function useToast() { return useContext(ToastCtx); }
function ToastHost({ children }) {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((t) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(x => [...x, { id, ...t }]);
    setTimeout(() => setToasts(x => x.filter(z => z.id !== id)), t.duration || 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {ReactDOM.createPortal(
        <div className="toast-wrap">
          {toasts.map(t => (
            <div className="toast" key={t.id}>
              <span style={{ color: `var(--${t.tone || 'accent'})`, marginTop: 1 }}>
                <Icon name={t.icon || 'check'} size={16} />
              </span>
              <div className="col gap-1" style={{ flex: 1 }}>
                <div className="t-sm" style={{ fontWeight: 500 }}>{t.title}</div>
                {t.sub && <div className="cap">{t.sub}</div>}
              </div>
            </div>
          ))}
        </div>, document.body)}
    </ToastCtx.Provider>
  );
}

Object.assign(window, {
  Icon, Button, Badge, Dot, Progress, SegProgress, Avatar, Modal, Drawer, Empty,
  ToastHost, useToast, ago, dur, STATUS_MAP,
  useState, useEffect, useRef, useCallback,
});
