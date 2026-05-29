export function LoadingState({ label = "Loading" }) {
  return (
    <div className="state-card row gap-2">
      <span className="state-spinner" aria-hidden="true" />
      <p className="muted">{label}</p>
    </div>
  );
}
