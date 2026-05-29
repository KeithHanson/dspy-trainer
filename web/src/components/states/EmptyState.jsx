export function EmptyState({ title, description }) {
  return (
    <div className="state-card col center">
      <p className="t-h2">{title}</p>
      <p className="muted">{description}</p>
    </div>
  );
}
