/**
 * The shape behind the Files sidebar: one group per scope, folders nested by path inside it.
 *
 * Two facts decide where a doc sits, and each has exactly one home. Which *project* it belongs to is
 * its own `project_id` — so a doc cannot be in a project's folder and not in the project. Which
 * *folder* within that project is its `folder` path ('Work/Research'; '' is the group's own root).
 * Paths are therefore unique per scope, not globally: two projects can each keep a "Research".
 *
 * Every project gets a group whether or not anything is filed in it yet, which is what "each project
 * automatically gets a folder" means here — it is derived from the project list, so it can never go
 * missing or go stale.
 *
 * Keeping this pure (and out of the component) is what lets the drag rules and the ordering be tested
 * without a DOM.
 */
import type { Doc, DocFolder } from '@shared/types'

/** '' is the personal tree; any other value is a project id. */
export type Scope = string
export const PERSONAL: Scope = ''

export interface TreeNode {
  scope: Scope
  path: string
  name: string
  /** Docs filed directly in this folder, in the order the API returned them (starred first). */
  docs: Doc[]
  children: TreeNode[]
  /** Docs anywhere beneath it, this folder included: what a collapsed row counts. */
  deep: number
  /** How many path segments deep it is: 0 for a folder at the group's root. */
  depth: number
}

export interface Group {
  scope: Scope
  name: string
  /** The project's colour, for the dot on the group row. Absent for Personal. */
  color?: string
  /** True when this group stands for a project that is gone but still named by a doc or a folder. */
  orphan?: boolean
  roots: TreeNode[]
  /** Docs in the group but in no folder. They render after the folders, like a desk's loose pages. */
  loose: Doc[]
  /** Every doc anywhere in the group. */
  deep: number
}

/** A project as the tree needs to know it. */
export interface GroupSource {
  id: string
  name: string
  color?: string
}

export const parentOf = (path: string): string => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
export const nameOf = (path: string): string => path.slice(path.lastIndexOf('/') + 1)
export const joinPath = (parent: string, name: string): string => (parent ? `${parent}/${name}` : name)

/**
 * Keys for the expand/collapse set, which is persisted and so has to survive both kinds of row.
 * A folder is closed until opened; a group is *open* until closed, because hiding a project behind a
 * disclosure you have to find is no way to be shown your own files. Hence the inverted key: what the
 * set remembers about a group is that you shut it.
 */
export const folderKey = (scope: Scope, path: string): string => `${scope}\u0000${path}`
export const groupShutKey = (scope: Scope): string => `!${scope}`

/** Is `path` the folder `maybeAncestor` itself, or anything beneath it? */
export const isInside = (path: string, maybeAncestor: string): boolean =>
  maybeAncestor === '' || path === maybeAncestor || path.startsWith(`${maybeAncestor}/`)

/**
 * A folder cannot be dropped on itself, on its own subtree (that would orphan what it carries), or on
 * the parent it is already in. Nor into another project: a folder's docs each carry their own
 * `project_id`, so moving the folder across would have to rewrite every one of them — that is a
 * per-doc move, and the UI says so by not offering the target. The backend refuses these too; the UI
 * just stops showing them as targets first.
 */
export const canDropFolder = (src: string, dest: string, srcScope: Scope = '', destScope: Scope = ''): boolean =>
  Boolean(src) && srcScope === destScope && !isInside(dest, src) && parentOf(src) !== dest

/** Can this doc land here? Anywhere it is not already — a different project, a different folder, or both. */
export const canDropDoc = (docScope: Scope, docFolder: string, destScope: Scope, destFolder: string): boolean =>
  docScope !== destScope || docFolder !== destFolder

/** Every folder on the path from the group's root down to `path`, so revealing a doc opens its chain. */
export const chainTo = (path: string): string[] => {
  const segs = path.split('/').filter(Boolean)
  return segs.map((_, i) => segs.slice(0, i + 1).join('/'))
}

const byName = (a: { name: string }, b: { name: string }): number => a.name.localeCompare(b.name, undefined, { numeric: true })

/** A doc's scope is its project, with no second source of truth to disagree with. */
export const scopeOf = (d: Doc): Scope => d.project_id ?? PERSONAL

/**
 * Build the groups. Folders come from the server (so an empty one still shows); a doc filed in a
 * folder the server has not listed still appears, under a folder synthesised for it — a doc is never
 * invisible because its folder row went missing. Likewise a doc whose project has been deleted gets
 * an `orphan` group rather than vanishing.
 */
export function buildGroups(folders: DocFolder[], docs: Doc[], projects: GroupSource[]): Group[] {
  const known = new Map(projects.map((p) => [p.id, p]))
  const nodes = new Map<string, TreeNode>()  // keyed by folderKey
  const scopes = new Set<Scope>([PERSONAL, ...projects.map((p) => p.id)])

  const ensure = (scope: Scope, path: string): TreeNode => {
    const key = folderKey(scope, path)
    const found = nodes.get(key)
    if (found) return found
    const node: TreeNode = {
      scope, path, name: nameOf(path), docs: [], children: [], deep: 0, depth: path.split('/').length - 1
    }
    nodes.set(key, node)
    // Synthesised ancestors, so a nested path never hangs off nothing.
    if (parentOf(path)) ensure(scope, parentOf(path))
    return node
  }

  for (const f of folders) {
    // A folder row for a project that is gone is noise, not content — unlike a doc, nothing is lost
    // by dropping it, and the backend clears these when a project is deleted.
    if (f.scope && !known.has(f.scope)) continue
    ensure(f.scope, f.path)
  }

  const bucket = <T,>(m: Map<Scope, T[]>, scope: Scope): T[] => {
    const got = m.get(scope)
    if (got) return got
    const made: T[] = []
    m.set(scope, made)
    return made
  }

  const loose = new Map<Scope, Doc[]>()
  for (const d of docs) {
    const scope = scopeOf(d)
    scopes.add(scope)
    if (!d.folder) bucket(loose, scope).push(d)
    else ensure(scope, d.folder).docs.push(d)
  }

  const rootsByScope = new Map<Scope, TreeNode[]>()
  for (const node of nodes.values()) {
    const parent = parentOf(node.path)
    if (parent) nodes.get(folderKey(node.scope, parent))?.children.push(node)
    else bucket(rootsByScope, node.scope).push(node)
  }

  // Depth-first, so a parent's `deep` counts what its children have already counted.
  const total = (node: TreeNode): number => {
    node.children.sort(byName)
    node.deep = node.docs.length + node.children.reduce((n, c) => n + total(c), 0)
    return node.deep
  }

  const groups: Group[] = []
  for (const scope of scopes) {
    const roots = rootsByScope.get(scope) ?? []
    roots.sort(byName)
    for (const r of roots) total(r)
    const own = loose.get(scope) ?? []
    const project = scope ? known.get(scope) : undefined
    groups.push({
      scope,
      name: scope ? (project?.name ?? 'Former project') : 'Personal',
      color: project?.color,
      orphan: Boolean(scope) && !project,
      roots,
      loose: own,
      deep: own.length + roots.reduce((n, r) => n + r.deep, 0)
    })
  }

  // Personal first — it is where a doc lands when nothing says otherwise — then the projects by name,
  // with anything left over from a deleted project at the end.
  return groups.sort((a, b) => {
    if (a.scope === PERSONAL) return -1
    if (b.scope === PERSONAL) return 1
    if (a.orphan !== b.orphan) return a.orphan ? 1 : -1
    return byName(a, b)
  })
}

/** Flattened rows for rendering: a group, then its folders (each with its docs), then its loose docs. */
export interface Row {
  kind: 'group' | 'folder' | 'doc'
  scope: Scope
  /** Indent level as rendered: a group is 0, so its contents start at 1. */
  depth: number
  group?: Group
  folder?: TreeNode
  doc?: Doc
}

export function flattenGroups(
  groups: Group[],
  openGroup: (scope: Scope) => boolean,
  openFolder: (scope: Scope, path: string) => boolean
): Row[] {
  const rows: Row[] = []
  const walk = (node: TreeNode): void => {
    rows.push({ kind: 'folder', scope: node.scope, depth: node.depth + 1, folder: node })
    if (!openFolder(node.scope, node.path)) return
    for (const d of node.docs) rows.push({ kind: 'doc', scope: node.scope, depth: node.depth + 2, doc: d })
    for (const c of node.children) walk(c)
  }
  for (const g of groups) {
    rows.push({ kind: 'group', scope: g.scope, depth: 0, group: g })
    if (!openGroup(g.scope)) continue
    for (const r of g.roots) walk(r)
    for (const d of g.loose) rows.push({ kind: 'doc', scope: g.scope, depth: 1, doc: d })
  }
  return rows
}
