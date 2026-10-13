/** What a view shows in place of "Loading…" when its fetch failed: the reason and a way to try again. */
export default function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }): JSX.Element {
  return (
    <div className="empty-state" role="alert">
      <p>Could not load: {message}</p>
      <button className="ghost-btn" onClick={onRetry}>Try again</button>
    </div>
  )
}
