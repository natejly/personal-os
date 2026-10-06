/**
 * Pure conversion of a Chrome accessibility tree into the text a model reads and a table of refs.
 * No Electron and no I/O here: the browser driver feeds in `Accessibility.getFullAXTree` nodes plus a few
 * facts the tree does not carry (input type, submit-ness, clickability, where the element sits relative to the
 * viewport), and gets back snapshot text and `ref -> backendDOMNodeId` entries that never leave the main process.
 *
 * The page is hostile input. Everything page-authored (names, text, title, url) is flattened to one line and has
 * the `<page` delimiter neutralised; the opening tag carries a random id so page text cannot forge the frame.
 */

export interface AXValue {
  type?: string
  value?: unknown
}
export interface AXProp {
  name: string
  value: AXValue
}
export interface AXNode {
  nodeId: string
  ignored?: boolean
  role?: AXValue
  name?: AXValue
  value?: AXValue
  properties?: AXProp[]
  childIds?: string[]
  parentId?: string
  backendDOMNodeId?: number
}

/** What the DOM knows and the accessibility tree does not. Keyed by backendDOMNodeId. */
export interface DomHint {
  tag: string
  type?: string
  autocomplete?: string
  submit?: boolean
  clickable?: boolean
  /** Position relative to the viewport; absent when the element has no box of its own. */
  view?: 'in' | 'above' | 'below'
}

export interface RefEntry {
  ref: string
  backendNodeId: number
  role: string
  name: string
}

export interface SnapshotInput {
  nodes: AXNode[]
  hints: Map<number, DomHint>
  pageId: string
  url: string
  title: string
  tab: number
  tabs: number
  scrollPct: number
  query?: string
  full?: boolean
  maxChars?: number
  /** Cross-origin frames the driver knows it could not read, added to those detected in the tree. */
  unreadableFrames?: number
}

export interface SnapshotOut {
  text: string
  refs: RefEntry[]
  interactive: number
  truncated: boolean
}

export const DEFAULT_MAX_CHARS = 8_000
export const HARD_MAX_CHARS = 12_000
export const DEFAULT_MAX_INTERACTIVE = 120
const MAX_LINE = 160
const MAX_NAME = 120
const MAX_TEXT_LINES = 40
const MAX_TEXT_LINES_FULL = 120
const MAX_TEXT_RUN = 6
const COLLAPSE_MIN = 4
const COLLAPSE_KEEP = 3

const INTERACTIVE = new Set([
  'button', 'link', 'textbox', 'searchbox', 'combobox', 'option', 'checkbox', 'radio', 'switch', 'slider', 'tab',
  'menuitem', 'menuitemcheckbox', 'menuitemradio', 'spinbutton', 'treeitem', 'DisclosureTriangle', 'PopUpButton',
  'ListBoxOption', 'MenuListOption'
])
/** Roles a script-made control hides behind; promoted to `clickable` only when the DOM says it has a handler. */
const CLICKABLE_HOSTS = new Set(['generic', 'group', 'image', 'img', 'listitem', 'paragraph', 'row', 'cell', 'gridcell', 'StaticText'])
const LANDMARKS = new Set(['main', 'navigation', 'banner', 'contentinfo', 'search', 'complementary', 'form', 'region', 'dialog', 'alertdialog'])
const VALUE_ROLES = new Set(['textbox', 'searchbox', 'combobox', 'spinbutton', 'slider', 'PopUpButton'])
const SKIP_ROLES = new Set(['LineBreak', 'InlineTextBox', 'ListMarker', 'none', 'presentation', 'Ignored'])
const ROLE_ALIAS: Record<string, string> = { ListBoxOption: 'option', MenuListOption: 'option', PopUpButton: 'combobox', DisclosureTriangle: 'button' }

type Kind = 'interactive' | 'heading' | 'landmark' | 'text'

interface Item {
  kind: Kind
  role: string
  name: string
  flags: string[]
  value?: string
  backendNodeId?: number
  parent: string
  view?: DomHint['view']
}

const str = (v: AXValue | undefined): string => (typeof v?.value === 'string' ? v.value : v?.value == null ? '' : String(v.value))

/** One line, no page-authored delimiter, bounded. */
export function clean(s: string, max: number): string {
  let t = s.replace(/[\u0000-\u001f\u007f\u0085]+/g, ' ').replace(/\s+/g, ' ').trim() // \s also covers U+2028/2029
  t = t.replace(/<(\/?)page/gi, '‹$1page')
  if (t.length > max) t = t.slice(0, max - 1).trimEnd() + '…'
  return t
}

const quote = (s: string): string => `"${s.replace(/"/g, "'")}"`

function prop(n: AXNode, name: string): unknown {
  return n.properties?.find((p) => p.name === name)?.value?.value
}
const truthy = (v: unknown): boolean => v === true || v === 'true'

function flagsFor(n: AXNode, role: string, hint: DomHint | undefined): string[] {
  const f: string[] = []
  const checked = prop(n, 'checked')
  if (truthy(checked)) f.push('checked')
  else if (checked === 'mixed') f.push('mixed')
  if (truthy(prop(n, 'selected'))) f.push('selected')
  if (truthy(prop(n, 'pressed'))) f.push('pressed')
  const expanded = prop(n, 'expanded')
  if (truthy(expanded)) f.push('expanded')
  else if (expanded === false || expanded === 'false') f.push('collapsed')
  if (truthy(prop(n, 'disabled'))) f.push('disabled')
  if (truthy(prop(n, 'required'))) f.push('required')
  if (truthy(prop(n, 'readonly'))) f.push('readonly')
  const invalid = prop(n, 'invalid')
  if (truthy(invalid) || (typeof invalid === 'string' && invalid !== 'false')) f.push('invalid')
  if (truthy(prop(n, 'modal'))) f.push('modal')
  if (hint?.type === 'password') f.push('password')
  if (hint?.submit) f.push('submit')
  if (role === 'heading') {
    const lvl = prop(n, 'level')
    if (typeof lvl === 'number' && lvl > 1) f.push(`h${lvl}`)
  }
  return f
}

function descendantText(n: AXNode, byId: Map<string, AXNode>, depth = 0): string {
  if (depth > 4) return ''
  let out = ''
  for (const c of n.childIds ?? []) {
    const k = byId.get(c)
    if (!k) continue
    out += ' ' + (str(k.role) === 'StaticText' ? str(k.name) : descendantText(k, byId, depth + 1))
    if (out.length > MAX_NAME) break
  }
  return out
}

/** Walks the tree in document order and returns flat items; no refs yet. */
function collect(nodes: AXNode[], hints: Map<number, DomHint>): { items: Item[]; emptyFrames: number } {
  const byId = new Map<string, AXNode>()
  for (const n of nodes) byId.set(n.nodeId, n)
  const isChild = new Set<string>()
  for (const n of nodes) for (const c of n.childIds ?? []) isChild.add(c)
  const items: Item[] = []
  let emptyFrames = 0
  const seen = new Set<string>()

  const visit = (id: string, parent: string, suppressText: boolean): void => {
    const n = byId.get(id)
    if (!n || seen.has(id)) return
    seen.add(id)
    const role = str(n.role)
    const kids = n.childIds ?? []
    const hint = n.backendDOMNodeId != null ? hints.get(n.backendDOMNodeId) : undefined
    let suppress = suppressText
    if (role === 'Iframe' || role === 'IframePresentational') {
      if (!kids.some((k) => byId.has(k))) emptyFrames++
    }
    if (!n.ignored && !SKIP_ROLES.has(role)) {
      const name = clean(str(n.name), MAX_NAME)
      if (INTERACTIVE.has(role) && n.backendDOMNodeId != null) {
        const it: Item = {
          kind: 'interactive', role: ROLE_ALIAS[role] ?? role, name, flags: flagsFor(n, role, hint),
          backendNodeId: n.backendDOMNodeId, parent, view: hint?.view
        }
        if (VALUE_ROLES.has(role) && hint?.type !== 'password') {
          const v = clean(str(n.value), 80)
          if (v) it.value = v
        }
        items.push(it)
        suppress = true
      } else if (!suppress && hint?.clickable && n.backendDOMNodeId != null && CLICKABLE_HOSTS.has(role) && !truthy(prop(n, 'disabled'))) {
        // A delegated listener on a big wrapper must not swallow the page: only short, self-contained labels qualify.
        const raw = name || descendantText(n, byId).trim()
        const label = raw.length <= 60 ? clean(raw, MAX_NAME) : ''
        if (label) {
          items.push({ kind: 'interactive', role: 'clickable', name: label, flags: [], backendNodeId: n.backendDOMNodeId, parent, view: hint.view })
          suppress = true
        }
      } else if (role === 'heading') {
        if (name) items.push({ kind: 'heading', role, name, flags: flagsFor(n, role, hint), parent })
        suppress = true
      } else if (LANDMARKS.has(role) && ((role !== 'region' && role !== 'form') || name)) {
        items.push({ kind: 'landmark', role, name, flags: flagsFor(n, role, hint), parent })
      } else if (role === 'StaticText') {
        if (!suppress && name) items.push({ kind: 'text', role: 'text', name, flags: [], parent })
      } else if ((role === 'image' || role === 'img') && name && !suppress) {
        items.push({ kind: 'text', role: 'image', name, flags: [], parent })
      }
    }
    for (const k of kids) visit(k, n.nodeId, suppress)
  }

  for (const n of nodes) if (!isChild.has(n.nodeId)) visit(n.nodeId, '', false)
  return { items, emptyFrames }
}

/** Consecutive text nodes under one parent are one visual run: join them into one line. */
function mergeText(items: Item[]): Item[] {
  const out: Item[] = []
  for (const it of items) {
    const prev = out[out.length - 1]
    const bothText = it.kind === 'text' && it.role === 'text' && prev && prev.kind === 'text' && prev.role === 'text'
    if (bothText && prev.parent === it.parent) {
      prev.name = clean(`${prev.name} ${it.name}`, MAX_LINE * 3)
      continue
    }
    if (bothText && prev.name === it.name) continue
    out.push({ ...it })
  }
  for (const it of out) if (it.kind === 'text') it.name = clean(it.name, MAX_LINE)
  return out
}

const shapeOf = (it: Item): string =>
  `${it.kind}|${it.role}|${it.parent}|${it.flags.join(',')}|${it.name.replace(/[0-9]+/g, '#')}|${it.value !== undefined}`

function lineText(it: Item): string {
  if (it.kind === 'text') return it.role === 'image' ? `image ${quote(it.name)}` : it.role === 'more' ? it.name : `text ${quote(it.name)}`
  let s = it.role
  if (it.name) s += ` ${quote(it.name)}`
  if (it.value !== undefined) s += ` = ${quote(it.value)}`
  for (const f of it.flags) s += ` [${f}]`
  return s
}

const searchable = (it: Item): string => `${it.role} ${it.name} ${it.value ?? ''}`.toLowerCase()

export function buildSnapshot(inp: SnapshotInput): SnapshotOut {
  const full = !!inp.full
  const charBudget = full ? HARD_MAX_CHARS : Math.max(1_000, Math.min(inp.maxChars ?? DEFAULT_MAX_CHARS, HARD_MAX_CHARS))
  const maxInteractive = full ? Infinity : DEFAULT_MAX_INTERACTIVE
  const maxText = full ? MAX_TEXT_LINES_FULL : MAX_TEXT_LINES

  const { items: raw, emptyFrames } = collect(inp.nodes, inp.hints)
  const all = mergeText(raw)
  const totalInteractive = all.filter((i) => i.kind === 'interactive').length
  const notes: string[] = []

  // 1. query: keep matching lines and the heading each sits under.
  let items = all
  const words = (inp.query ?? '').toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length) {
    const keep = new Set<number>()
    all.forEach((it, i) => {
      if (!words.some((w) => searchable(it).includes(w))) return
      keep.add(i)
      for (let j = i - 1; j >= 0; j--) if (all[j].kind === 'heading') { keep.add(j); break }
    })
    items = all.filter((_, i) => keep.has(i))
    if (!items.length) notes.push(`no line matches "${clean(inp.query ?? '', 60)}"`)
  }

  // 2. collapse runs of identical sibling lines (not when the caller asked for everything).
  let collapsed = 0
  if (!full) {
    const out: Item[] = []
    let i = 0
    while (i < items.length) {
      const first = items[i]
      let j = i + 1
      if (first.kind === 'interactive' || first.kind === 'text') {
        const key = shapeOf(first)
        while (j < items.length && items[j].kind === first.kind && shapeOf(items[j]) === key) j++
      }
      if (j - i >= COLLAPSE_MIN) {
        for (let k = i; k < i + COLLAPSE_KEEP; k++) out.push(items[k])
        const more = j - i - COLLAPSE_KEEP
        collapsed += more
        out.push({ kind: 'text', role: 'more', name: `… ${more} more similar`, flags: [], parent: first.parent })
        i = j
      } else {
        out.push(first)
        i++
      }
    }
    items = out
  }

  // 3. caps. Interactive lines: keep what is in view first, then fill from the rest in document order.
  const interIdx = items.flatMap((it, i) => (it.kind === 'interactive' ? [i] : []))
  const drop = new Set<number>()
  let below = 0
  let above = 0
  if (interIdx.length > maxInteractive) {
    const keep = new Set<number>()
    for (const i of interIdx) if (keep.size < maxInteractive && items[i].view !== 'above' && items[i].view !== 'below') keep.add(i)
    for (const i of interIdx) if (keep.size < maxInteractive) keep.add(i)
    for (const i of interIdx) {
      if (keep.has(i)) continue
      drop.add(i)
      if (items[i].view === 'above') above++
      else below++
    }
  }
  let textSeen = 0
  let run = 0
  let textOmitted = 0
  items.forEach((it, i) => {
    if (it.kind !== 'text' || it.role === 'more') {
      run = 0
      return
    }
    run++
    textSeen++
    if (textSeen > maxText || (!full && run > MAX_TEXT_RUN)) {
      drop.add(i)
      textOmitted++
    }
  })

  // 4. char budget: header and footer are reserved, lines are taken in order until it is spent.
  const headerLines = [
    `url: ${clean(inp.url, 300)}`,
    `title: ${clean(inp.title, 200)}`,
    `tab ${inp.tab} of ${inp.tabs} · scrolled ${Math.max(0, Math.min(100, Math.round(inp.scrollPct)))}% · ${totalInteractive} interactive`,
    `<page id="${inp.pageId}">`
  ]
  let used = headerLines.join('\n').length + '\n</page>\n'.length + 220
  const kept: Item[] = []
  let charCut = false
  let charInteractive = 0
  for (let i = 0; i < items.length; i++) {
    if (drop.has(i)) continue
    const it = items[i]
    if (charCut || used + lineText(it).length + 6 > charBudget) {
      charCut = true // contiguous cut: nothing after the first overflow is shown
      if (it.kind === 'interactive') charInteractive++
      continue
    }
    used += lineText(it).length + 6
    kept.push(it)
  }

  // 5. refs, in final order.
  const refs: RefEntry[] = []
  const lines: string[] = []
  for (const it of kept) {
    if (it.kind === 'interactive' && it.backendNodeId != null) {
      const ref = `e${refs.length + 1}`
      refs.push({ ref, backendNodeId: it.backendNodeId, role: it.role, name: it.name })
      lines.push(`${ref} ${lineText(it)}`)
    } else {
      lines.push(lineText(it))
    }
  }

  const plural = (n: number, w: string): string => `${n} ${w}${n === 1 ? '' : 's'}`
  const unread = emptyFrames + (inp.unreadableFrames ?? 0)
  const footer: string[] = []
  if (below) footer.push(`${plural(below, 'more interactive element')} below`)
  if (above) footer.push(`${plural(above, 'more interactive element')} above`)
  if (charInteractive) footer.push(`${plural(charInteractive, 'more interactive element')} cut to fit ${charBudget} characters`)
  else if (charCut) footer.push(`text cut to fit ${charBudget} characters`)
  if (textOmitted) footer.push(`${plural(textOmitted, 'text line')} omitted`)
  if (collapsed) footer.push(`${plural(collapsed, 'similar line')} collapsed`)
  if (unread) footer.push(`${plural(unread, 'frame')} could not be read`)
  footer.push(...notes)
  if (below + above + charInteractive > 0 && !words.length) footer.push('use query or full to see the rest')

  const text = [...headerLines, ...lines, '</page>', ...(footer.length ? [footer.join('; ')] : [])].join('\n')
  return { text, refs, interactive: totalInteractive, truncated: !!(below || above || charCut || textOmitted) }
}
