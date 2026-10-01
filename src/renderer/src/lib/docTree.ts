/**
 * The shape behind the Docs sidebar: folders nested by path, each holding its own docs.
 *
 * Folder paths are the source of truth on both sides — a doc's `folder` is the full path of the
 * folder it sits in ('Work/Research'), and '' means the root. Keeping this pure (and out of the
 * component) is what lets the drag rules and the ordering be tested without a DOM.
 */
import type { Doc, DocFolder } from '@shared/types'

export interface TreeNode {
  path: string
  name: string
  /** Docs filed directly in this folder, in the order the API returned them (starred first). */
  docs: Doc[]
  children: TreeNode[]
  /** Docs anywhere beneath it, this folder included: what a collapsed row counts. */
  deep: number
  depth: number
}

export interface DocTree {
  roots: TreeNode[]
  /** Docs in no folder at all. They render after the folders, like a desk's loose pages. */
  loose: Doc[]
}

export const parentOf = (path: string): string => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
export const nameOf = (path: string): string => path.slice(path.lastIndexOf('/') + 1)
export const joinPath = (parent: string, name: string): string => (parent ? `${parent}/${name}` : name)

/** Is `path` the folder `maybeAncestor` itself, or anything beneath it? */
export const isInside = (path: string, maybeAncestor: string): boolean =>
  maybeAncestor === '' || path === maybeAncestor || path.startsWith(`${maybeAncestor}/`)

/**
 * A folder cannot be dropped on itself, on its own subtree (that would orphan what it carries), or
 * on the parent it is already in. The backend refuses these too; the UI just stops showing them as
 * targets first.
 */
export const canDropFolder = (src: string, dest: string): boolean =>
  Boolean(src) && !isInside(dest, src) && parentOf(src) !== dest

/** Every folder on the path from the root down to `path`, so revealing a doc opens its whole chain. */
export const chainTo = (path: string): string[] => {
  const segs = path.split('/').filter(Boolean)
  return segs.map((_, i) => segs.slice(0, i + 1).join('/'))
}

const byName = (a: TreeNode, b: TreeNode): number => a.name.localeCompare(b.name, undefined, { numeric: true })

/**
 * Build the tree. Folders come from the server (so an empty one still shows); a doc filed in a
 * folder the server has not listed still appears, under a folder synthesised for it — a doc is never
 * invisible because its folder row went missing.
 */
export function buildTree(folders: DocFolder[], docs: Doc[]): DocTree {
  const nodes = new Map<string, TreeNode>()
  const ensure = (path: string): TreeNode => {
    const found = nodes.get(path)
    if (found) return found
    const node: TreeNode = { path, name: nameOf(path), docs: [], children: [], deep: 0, depth: path.split('/').length - 1 }
    nodes.set(path, node)
    // Synthesised ancestors, so a nested path never hangs off nothing.
    if (parentOf(path)) ensure(parentOf(path))
    return node
  }

  for (const f of folders) ensure(f.path)

  const loose: Doc[] = []
  for (const d of docs) {
    if (!d.folder) loose.push(d)
    else ensure(d.folder).docs.push(d)
  }

  const roots: TreeNode[] = []
  for (const node of nodes.values()) {
    const parent = parentOf(node.path)
    if (parent) nodes.get(parent)?.children.push(node)
    else roots.push(node)
  }

  // Depth-first, so a parent's `deep` counts what its children have already counted.
  const total = (node: TreeNode): number => {
    node.children.sort(byName)
    node.deep = node.docs.length + node.children.reduce((n, c) => n + total(c), 0)
    return node.deep
  }
  roots.sort(byName)
  for (const r of roots) total(r)

  return { roots, loose }
}

/** Flattened rows for rendering: a folder, then its docs, then its children — parents before kids. */
export interface Row {
  kind: 'folder' | 'doc'
  depth: number
  folder?: TreeNode
  doc?: Doc
}

export function flatten(tree: DocTree, expanded: (path: string) => boolean): Row[] {
  const rows: Row[] = []
  const walk = (node: TreeNode): void => {
    rows.push({ kind: 'folder', depth: node.depth, folder: node })
    if (!expanded(node.path)) return
    for (const d of node.docs) rows.push({ kind: 'doc', depth: node.depth + 1, doc: d })
    for (const c of node.children) walk(c)
  }
  for (const r of tree.roots) walk(r)
  for (const d of tree.loose) rows.push({ kind: 'doc', depth: 0, doc: d })
  return rows
}
