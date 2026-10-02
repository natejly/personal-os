/**
 * Pure helpers for the agent browser: accessibility-tree nodes -> the text a model reads plus a ref table.
 * Nothing here touches Electron or the page, so the whole contract (refs, flags, caps, collapse, delimiters)
 * is unit-tested. The driver (agentBrowser.ts) feeds it `Accessibility.getFullAXTree` nodes and per-node
 * hints distilled from `DOMSnapshot.captureSnapshot`, because the AX tree alone does not say whether an
 * input is a password field or a button submits a form.
 */

export interface AxProp {
  name: string
  value?: { value?: unknown }
}
export interface AxNode {
  nodeId: string
  ignored?: boolean
  role?: { value?: unknown }
  name?: { value?: unknown }
  value?: { value?: unknown }
  properties?: AxProp[]
  childIds?: string[]
  parentId?: string
  backendDOMNodeId?: number
}

/** What the DOM knows about a node that the AX tree does not. All optional: a missing hint just drops a flag. */
export interface NodeHint {
  tag?: string
  inputType?: string
  autocomplete?: string
  clickable?: boolean
  /** A control that submits its form when activated. */
  submit?: boolean
  inForm?: boolean
  formAction?: string
  href?: string
  download?: boolean
}

export interface SnapshotMeta {
  url: string
  title: string
  tab: number
  tabs: number
  scrollPct: number
  /** Random per snapshot, chosen by the caller (kept out of here so output is deterministic under test). */
  pageId: string
  query?: string
  full?: boolean
  maxChars?: number
  /** Frames the driver already knows it could not read (cross-process iframes it did not attach to). */
  unreadableFrames?: number
}

export interface RefEntry {
  ref: string
  backendNodeId: number
  role: string
  name: string
  hint?: NodeHint
}

export interface SnapshotOut {
  text: string
  refs: RefEntry[]
  /** Interactive elements on the page before any cap or filter. */
  interactive: number
  truncated: boolean
}

export const DEFAULT_MAX_CHARS = 8_000
export const HARD_MAX_CHARS = 12_000
export const DEFAULT_MAX_INTERACTIVE = 120
const MAX_LINE = 160
const MAX_TEXT_LINES = 40
const TEXT_RUN_KEEP = 3
const TEXT_RUN_LIMIT = 5
const COLLAPSE_MIN = 4
const COLLAPSE_KEEP = 3

const INTERACTIVE = new Set([
  'button', 'link', 'textbox', 'textfield', 'searchbox', 'combobox', 'popupbutton', 'option', 'listboxoption', 'checkbox', 'radio',
  'switch', 'slider', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'spinbutton', 'treeitem', 'disclosuretriangle'
])
const VALUE_ROLES = new Set(['textbox', 'textfield', 'searchbox', 'combobox', 'popupbutton', 'spinbutton', 'slider'])
const LANDMARKS = new Set(['banner', 'navigation', 'main', 'complementary', 'contentinfo', 'search', 'form', 'region', 'dialog', 'alertdialog'])
const SKIPPED = new Set(['none', 'presentation', 'generic', 'inlinetextbox', 'linebreak', 'rootwebarea', 'webarea'])

const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '')
const flat = (s: string): string => s.replace(/[\u0000-\u001f\u007f ]+/g, ' ').replace(/\s+/g, ' ').trim()
const clip = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

/** Page text must never be able to forge a delimiter: no `<page` / `</page` survives, and quotes are escaped in names. */
export function sanitize(s: string): string {
  return flat(s).replace(/<(\/?)\s*page\b/gi, '‹$1page')
}
const quoted = (s: string, n: number): string => `"${clip(sanitize(s), n).replace(/"/g, '\\"')}"`

function prop(n: AxNode, name: string): unknown {
  return n.properties?.find((p) => p.name === name)?.value?.value
}
const truthy = (v: unknown): boolean => v === true || v === 'true'

type Entry =
  | { kind: 'i'; role: string; name: string; flags: string[]; value: string; node: number; hint?: NodeHint; parent: string; heading: number }
  | { kind: 'h' | 'l'; text: string }
  | { kind: 't'; text: string }
  | { kind: 'more'; text: string; count: number }

const isSecret = (hint: NodeHint | undefined, name: string, role: string): boolean =>
  hint?.inputType === 'password' || /^cc-|one-time-code/.test(hint?.autocomplete ?? '') || (role === 'textbox' && /password|passcode|cvv|card number/i.test(name))

/** Walk the tree in document order and flatten it to lines, before any cap, filter or ref numbering. */
function collect(nodes: AxNode[], hints: Map<number, NodeHint>): { entries: Entry[]; unreadable: number } {
  const byId = new Map(nodes.map((n) => [n.nodeId, n]))
  const roots = nodes.filter((n) => !n.parentId || !byId.has(n.parentId))
  const entries: Entry[] = []
  let unreadable = 0
  let heading = -1
  // Explicit stack: real pages nest deeper than is safe to recurse.
  const stack: Array<{ n: AxNode; mute: boolean }> = roots.slice().reverse().map((n) => ({ n, mute: false }))
  const seen = new Set<string>()
  while (stack.length) {
    const { n, mute } = stack.pop()!
    if (seen.has(n.nodeId)) continue
    seen.add(n.nodeId)
    const role = str(n.role?.value).toLowerCase()
    const name = flat(str(n.name?.value))
    const kids = (n.childIds ?? []).map((id) => byId.get(id)).filter((k): k is AxNode => !!k)
    const descend = (m: boolean): void => {
      for (let i = kids.length - 1; i >= 0; i--) stack.push({ n: kids[i], mute: m })
    }
    if (n.ignored) { descend(mute); continue }
    const hint = n.backendDOMNodeId != null ? hints.get(n.backendDOMNodeId) : undefined
    const clickable = !!hint?.clickable && (role === 'generic' || role === '') && !!prop(n, 'focusable') && !!name
    if ((INTERACTIVE.has(role) || clickable) && n.backendDOMNodeId != null) {
      const flags: string[] = []
      const checked = prop(n, 'checked')
      if (truthy(checked)) flags.push('checked')
      else if (checked === 'mixed') flags.push('mixed')
      if (truthy(prop(n, 'selected'))) flags.push('selected')
      if (truthy(prop(n, 'disabled'))) flags.push('disabled')
      if (truthy(prop(n, 'expanded'))) flags.push('expanded')
      if (truthy(prop(n, 'required'))) flags.push('required')
      if (hint?.inputType === 'password') flags.push('password')
      if (hint?.submit) flags.push('submit')
      let value = ''
      if (VALUE_ROLES.has(role) && !isSecret(hint, name, role)) value = flat(str(n.value?.value))
      entries.push({ kind: 'i', role: clickable ? 'clickable' : role === 'textfield' ? 'textbox' : role === 'popupbutton' ? 'combobox' : role,
        name, flags, value, node: n.backendDOMNodeId, hint, parent: n.parentId ?? '', heading })
      continue // children (a native select's options, a link's text) are not separate targets
    }
    if (role === 'heading') {
      if (name) { entries.push({ kind: 'h', text: `heading ${quoted(name, MAX_LINE)}` }); heading = entries.length - 1 }
      descend(true) // links inside a heading still count; its text is already shown
      continue
    }
    if (LANDMARKS.has(role)) {
      entries.push({ kind: 'l', text: name ? `${role} ${quoted(name, 80)}` : role })
      descend(mute)
      continue
    }
    if (role === 'statictext' || role === 'text') {
      if (!mute && name) {
        const last = entries[entries.length - 1]
        if (last?.kind === 't' && last.text.length + name.length < MAX_LINE) last.text += ` ${name}`
        else entries.push({ kind: 't', text: name })
      }
      continue
    }
    if (role === 'iframe' && kids.length === 0) { unreadable++; continue }
    if (SKIPPED.has(role) || !role) { descend(mute); continue }
    descend(mute)
  }
  return { entries, unreadable }
}

/** Runs of 5+ prose lines keep their first few; the rest only inflate the page. */
function squashText(entries: Entry[]): Entry[] {
  const out: Entry[] = []
  let i = 0
  while (i < entries.length) {
    if (entries[i].kind !== 't') { out.push(entries[i++]); continue }
    let j = i
    while (j < entries.length && entries[j].kind === 't') j++
    const run = entries.slice(i, j)
    if (run.length > TEXT_RUN_LIMIT) {
      out.push(...run.slice(0, TEXT_RUN_KEEP), { kind: 'more', text: `… ${run.length - TEXT_RUN_KEEP} more text lines`, count: 0 })
    } else out.push(...run)
    i = j
  }
  return out
}

const shape = (e: Entry & { kind: 'i' }): string => `${e.parent}|${e.role}|${e.flags.join(',')}|${e.name.toLowerCase().replace(/\d+/g, '#')}`

/** 4+ adjacent siblings of one role whose names differ only in digits become the first 3 plus a count. */
function collapseSimilar(entries: Entry[]): { entries: Entry[]; collapsed: number } {
  const out: Entry[] = []
  let collapsed = 0
  let i = 0
  while (i < entries.length) {
    const e = entries[i]
    if (e.kind !== 'i') { out.push(e); i++; continue }
    let j = i + 1
    while (j < entries.length) {
      const f = entries[j]
      if (f.kind !== 'i' || shape(f) !== shape(e)) break
      j++
    }
    const n = j - i
    if (n >= COLLAPSE_MIN) {
      out.push(...entries.slice(i, i + COLLAPSE_KEEP), { kind: 'more', text: `… ${n - COLLAPSE_KEEP} more similar`, count: n - COLLAPSE_KEEP })
      collapsed += n - COLLAPSE_KEEP
    } else out.push(...entries.slice(i, j))
    i = j
  }
  return { entries: out, collapsed }
}

const lineText = (e: Entry): string => (e.kind === 'i' ? `${e.role} ${e.name} ${e.value}` : e.kind === 't' ? e.text : e.text).toLowerCase()

/** Keep lines containing any query word, each with the nearest heading above it. */
function filterQuery(entries: Entry[], query: string): Entry[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return entries
  const keep = new Set<number>()
  let lastHeading = -1
  entries.forEach((e, idx) => {
    if (e.kind === 'h') lastHeading = idx
    if (e.kind === 'more') return
    const text = lineText(e)
    if (words.some((w) => text.includes(w))) {
      keep.add(idx)
      if (lastHeading >= 0 && e.kind !== 'h') keep.add(lastHeading)
    }
  })
  return entries.filter((_e, idx) => keep.has(idx))
}

function render(e: Entry, ref: string | null): string {
  if (e.kind === 'i') {
    const flags = e.flags.length ? ` [${e.flags.join('] [')}]` : ''
    const value = e.value ? ` = ${quoted(e.value, 60)}` : ''
    return `${ref ? `${ref} ` : ''}${e.role}${e.name ? ` ${quoted(e.name, 100)}` : ''}${flags}${value}`
  }
  if (e.kind === 't') return `text ${quoted(e.text, MAX_LINE)}`
  return e.text
}

export function buildSnapshot(nodes: AxNode[], hints: Map<number, NodeHint>, meta: SnapshotMeta): SnapshotOut {
  const maxChars = Math.max(1_000, Math.min(meta.maxChars ?? DEFAULT_MAX_CHARS, HARD_MAX_CHARS))
  const maxInteractive = meta.full ? Number.POSITIVE_INFINITY : DEFAULT_MAX_INTERACTIVE
  const { entries: raw, unreadable } = collect(nodes, hints)
  const interactive = raw.filter((e) => e.kind === 'i').length
  let entries = squashText(raw)
  if (meta.query?.trim()) entries = filterQuery(entries, meta.query)
  let collapsed = 0
  if (!meta.full) ({ entries, collapsed } = collapseSimilar(entries))

  const header = [
    `url: ${sanitize(meta.url)}`,
    `title: ${clip(sanitize(meta.title), 200)}`,
    `tab ${meta.tab} of ${meta.tabs} · scrolled ${Math.round(meta.scrollPct)}% · ${interactive} interactive`,
    `<page id="${meta.pageId}">`
  ]
  const close = '</page>'
  const refs: RefEntry[] = []
  const lines: string[] = []
  let used = header.join('\n').length + close.length + 240 // room for the footer
  let notShown = 0
  let textLines = 0
  let textDropped = false
  let full = false
  for (const e of entries) {
    if (e.kind === 'i') {
      if (full || refs.length >= maxInteractive) { notShown++; continue }
      const ref = `e${refs.length + 1}`
      const line = render(e, ref)
      if (used + line.length + 1 > maxChars) { full = true; notShown++; continue }
      used += line.length + 1
      refs.push({ ref, backendNodeId: e.node, role: e.role, name: e.name, hint: e.hint })
      lines.push(line)
      continue
    }
    if (e.kind === 't') {
      if (textLines >= MAX_TEXT_LINES) { textDropped = true; continue }
      textLines++
    }
    if (full) { if (e.kind === 't') textDropped = true; continue }
    const line = render(e, null)
    if (used + line.length + 1 > maxChars) { full = true; if (e.kind === 't') textDropped = true; continue }
    used += line.length + 1
    lines.push(line)
  }
  const unread = unreadable + (meta.unreadableFrames ?? 0)
  const bits: string[] = []
  if (notShown) bits.push(`${notShown} more interactive element${notShown === 1 ? '' : 's'} not shown (use query, scroll, or full)`)
  if (collapsed) bits.push(`${collapsed} similar element${collapsed === 1 ? '' : 's'} collapsed (use full)`)
  if (textDropped) bits.push('some text omitted')
  if (unread) bits.push(`${unread} frame${unread === 1 ? '' : 's'} could not be read`)
  if (meta.query?.trim()) bits.push(`filtered by "${sanitize(meta.query)}"`)
  const text = [...header, ...lines, close, ...(bits.length ? [bits.join('; ')] : [])].join('\n')
  return { text, refs, interactive, truncated: notShown > 0 || textDropped || full }
}

// ---------------------------------------------------------------------------------------------------------------
// DOMSnapshot -> hints

interface DomDocument {
  nodes: {
    nodeName?: number[]
    attributes?: number[][]
    backendNodeId?: number[]
    parentIndex?: number[]
    isClickable?: { index?: number[] }
  }
  scrollOffsetY?: number
  contentHeight?: number
}
export interface DomSnapshot {
  documents: DomDocument[]
  strings: string[]
}

/** One pass over `DOMSnapshot.captureSnapshot` output: backendNodeId -> the hints the AX tree cannot give. */
export function hintsFromDomSnapshot(snap: DomSnapshot): { hints: Map<number, NodeHint>; scrollPct: number } {
  const hints = new Map<number, NodeHint>()
  const S = (i: number | undefined): string => (i == null || i < 0 ? '' : (snap.strings[i] ?? ''))
  for (const doc of snap.documents) {
    const nn = doc.nodes
    const ids = nn.backendNodeId ?? []
    const clickable = new Set(nn.isClickable?.index ?? [])
    const formOf: number[] = []
    const formAction: Record<number, string> = {}
    const attrsOf = (i: number): Record<string, string> => {
      const out: Record<string, string> = {}
      const a = nn.attributes?.[i] ?? []
      for (let k = 0; k + 1 < a.length; k += 2) out[S(a[k]).toLowerCase()] = S(a[k + 1])
      return out
    }
    for (let i = 0; i < ids.length; i++) {
      const tag = S(nn.nodeName?.[i]).toLowerCase()
      const parent = nn.parentIndex?.[i] ?? -1
      formOf[i] = tag === 'form' ? i : parent >= 0 ? (formOf[parent] ?? -1) : -1
      if (tag === 'form') formAction[i] = attrsOf(i).action ?? ''
      if (!['input', 'button', 'a', 'select', 'textarea', 'div', 'span', 'li', 'summary', 'label', 'img'].includes(tag) && !clickable.has(i)) continue
      const a = attrsOf(i)
      const h: NodeHint = { tag }
      if (clickable.has(i)) h.clickable = true
      if (tag === 'input') h.inputType = (a.type ?? 'text').toLowerCase()
      if (tag === 'button') h.inputType = (a.type ?? 'submit').toLowerCase()
      if (a.autocomplete) h.autocomplete = a.autocomplete.toLowerCase()
      if (a.href !== undefined) h.href = a.href
      if ('download' in a) h.download = true
      const f = formOf[i]
      if (f >= 0) { h.inForm = true; h.formAction = formAction[f] }
      h.submit = (tag === 'input' && (h.inputType === 'submit' || h.inputType === 'image')) || (tag === 'button' && f >= 0 && h.inputType === 'submit')
      hints.set(ids[i], h)
    }
  }
  const d = snap.documents[0]
  const scrollPct = d?.contentHeight && d.contentHeight > 0 ? Math.min(100, Math.max(0, ((d.scrollOffsetY ?? 0) / Math.max(1, d.contentHeight - 800)) * 100)) : 0
  return { hints, scrollPct }
}

// ---------------------------------------------------------------------------------------------------------------
// preview risk

export type Risk = 'none' | 'submit' | 'password' | 'payment' | 'download' | 'upload'
export type ActAction = 'click' | 'type' | 'select' | 'press'

/** What would this action do to the outside world? Ordered by how much the user should care. */
export function riskOf(action: ActAction, hint: NodeHint | undefined, o: { key?: string; submit?: boolean } = {}): Risk {
  const h = hint ?? {}
  const payment = /^cc-/.test(h.autocomplete ?? '')
  if ((action === 'type' || action === 'select') && payment) return 'payment'
  if (action === 'type' && h.inputType === 'password') return 'password'
  if (action === 'type' && o.submit) return 'submit'
  if (action === 'click') {
    if (h.inputType === 'file') return 'upload'
    if (h.download) return 'download'
    if (h.submit) return 'submit'
  }
  if (action === 'press' && /^enter$/i.test(o.key ?? '') && h.inForm && h.tag !== 'textarea' && h.tag !== 'button' && h.tag !== 'a') return 'submit'
  if (action === 'press' && /^enter$/i.test(o.key ?? '') && h.submit) return 'submit'
  return 'none'
}
