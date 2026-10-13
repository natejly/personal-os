/** A `/btw` question and its answer: a muted inline note, kept in the chat but never shown to the running reply. */
export default function SideNote({ content }: { content: string }): JSX.Element {
  let q = '', a = content
  try { ({ q, a } = JSON.parse(content) as { q: string; a: string }) } catch { /* plain text */ }
  return (
    <div className="side-note">
      <div><b>btw</b> {q}</div>
      <div>{a}</div>
    </div>
  )
}
