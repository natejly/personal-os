/** The hidden note a fork carries: what the abandoned branch did after the fork point. Collapsed, muted. */
export default function BranchNote({ content }: { content: string }): JSX.Element {
  const text = content.replace(/^[^\n]*\n```\n?/, '').replace(/\n?```$/, '')
  return (
    <details className="branch-note">
      <summary>Earlier branch summarised</summary>
      <div>{text}</div>
    </details>
  )
}
