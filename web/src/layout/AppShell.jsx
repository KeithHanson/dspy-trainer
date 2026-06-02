import { NavLink, useLocation } from "react-router-dom";
import { Icon } from "../components/Icon";
import { Button } from "../components/primitives/Button";

const PRIMARY_NAV = [
  { to: "/dashboard", label: "Overview", icon: "grid" },
  { to: "/lm-profiles", label: "LM Profiles", icon: "settings" },
  { to: "/bundles", label: "Module Bundles", icon: "box" },
  { to: "/plans", label: "Evaluation Plans", icon: "layers" },
  { to: "/optimization", label: "Optimization", icon: "search" },
  { to: "/runs", label: "Eval Jobs", icon: "activity" },
];

const SECONDARY_NAV = [
  { to: "/team", label: "Team", icon: "users" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

const BREADCRUMB_LABELS = {
  "/dashboard": "Overview",
  "/bundles": "Module Bundles",
  "/plans": "Evaluation Plans",
  "/lm-profiles": "LM Profiles",
  "/optimization": "Optimization",
  "/runs": "Eval Jobs",
  "/team": "Team",
  "/settings": "Settings",
};

function NavSection({ items }) {
  return items.map((item) => (
    <NavLink
      key={item.to}
      className={({ isActive }) => (isActive ? "shell-nav-item shell-nav-item-active" : "shell-nav-item")}
      to={item.to}
    >
      {({ isActive }) => (
        <>
          <Icon className="shell-nav-icon" name={item.icon} size={item.icon === "settings" ? 18 : 16} active={isActive} />
          <span>{item.label}</span>
          {item.to === "/runs" ? <span className="dot d-live" aria-hidden="true" /> : null}
        </>
      )}
    </NavLink>
  ));
}

function Breadcrumbs({ orgName }) {
  const { pathname } = useLocation();
  const section = BREADCRUMB_LABELS[pathname];
  const crumbs = section ? [orgName, section] : [orgName];

  return (
    <nav aria-label="Breadcrumb" className="row gap-2 shell-crumbs">
      {crumbs.map((crumb, index) => (
        <span key={`${crumb}-${index}`} className="row gap-2">
          {index > 0 ? <Icon name="chevR" size={13} className="faint" /> : null}
          <span className={index === crumbs.length - 1 ? "shell-crumb-active" : "muted"}>{crumb}</span>
        </span>
      ))}
    </nav>
  );
}

export function AppShell({ children, onSignOut, user, orgName = "Default" }) {
  const name = user?.name ?? "Authenticated User";
  const email = user?.email ?? "";
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "AU";

  return (
    <div className="shell-root">
      <aside className="shell-sidebar col">
        <div className="shell-org row gap-3">
          <div className="shell-logo center">
            <Icon name="bolt" size={14} />
          </div>
          <div className="col gap-1">
            <span className="shell-org-name">{orgName}</span>
            <span className="t-label">dspy-trainer</span>
          </div>
          <Icon name="chevD" size={13} className="faint" />
        </div>

        <div className="col shell-nav-wrap">
          <NavSection items={PRIMARY_NAV} />
          <hr className="hr shell-divider" />
          <NavSection items={SECONDARY_NAV} />
        </div>

        <footer className="row gap-3 shell-user">
          <div className="avatar">{initials}</div>
          <div className="col shell-user-meta">
            <span className="shell-user-name">{name}</span>
            <span className="faint">{email}</span>
          </div>
          <Button aria-label="Sign out" onClick={onSignOut} variant="ghost" size="sm" icon="logout" />
        </footer>
      </aside>

      <main className="shell-main col">
        <header className="shell-topbar row between">
          <Breadcrumbs orgName={orgName} />
        </header>
        <section className="shell-content">{children}</section>
      </main>
    </div>
  );
}
