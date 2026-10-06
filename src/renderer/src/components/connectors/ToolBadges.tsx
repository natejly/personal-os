/** What a server says about one of its own tools. Both are self-reported: nothing here changes how a tool is gated. */
export const CLAIM_NOTE = "The server's own claim, not checked by Grain."

export default function ToolBadges({ readOnly, destructive }: { readOnly?: boolean; destructive?: boolean }): JSX.Element | null {
  if (!readOnly && !destructive) return null
  return (
    <>
      {readOnly && <span className="tag ok" title={`Says it only reads. ${CLAIM_NOTE}`}>Read-only</span>}
      {destructive && <span className="tag bad" title={`Says it can change or delete things. ${CLAIM_NOTE}`}>Destructive</span>}
    </>
  )
}
