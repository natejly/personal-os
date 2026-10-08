/**
 * Which sidebar project groups show their chats. Folded groups are stored as exceptions, so a new
 * project starts open. A group is open unless the user folded it — including the group you are in:
 * folding it keeps its header (and the way back), so the explicit choice wins.
 */

export function projectGroupOpen(id: string, collapsed: ReadonlySet<string>): boolean {
  return !collapsed.has(id)
}

/** Fold or unfold one group, rebuilt from the live projects so a deleted project's id never lingers. */
export function toggleProjectGroup(collapsed: ReadonlySet<string>, id: string, liveIds: ReadonlySet<string>): Set<string> {
  const next = new Set([...collapsed].filter((x) => liveIds.has(x)))
  if (!next.delete(id)) next.add(id)
  return next
}
