export function ErrorState({ title = "Something went wrong", description = "Please try again." }) {
  return (
    <div className="state-card col center">
      <p className="t-h2">{title}</p>
      <p className="muted">{description}</p>
    </div>
  );
}
