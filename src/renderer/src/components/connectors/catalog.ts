import {
  BarChart3, BookOpen, Bot, Box, Brain, Briefcase, Calendar, Cloud, Code, CreditCard, Database, FileText, Figma, Folder,
  Github, Gitlab, Globe, HardDrive, Image, Link, Mail, MapPin, MessageSquare, Palette, Plug, Search, Server, Slack,
  Terminal, Wrench, Activity, Bug, ChartLine, CheckSquare, Chrome, Clock, GitBranch, Kanban, Layers, LayoutDashboard,
  ListChecks, Notebook, Receipt, Sparkles, Table, Triangle, Wallet, Zap, type LucideIcon
} from 'lucide-react'
import type { McpCatalogEntry, McpRegistryResult, McpServer } from '@shared/types'

/** A connector that is coming up gets polled at this interval; one that has settled does not. */
export const POLL_MS = 2500

/** Registry hits asked for per search. */
export const REGISTRY_LIMIT = 20

/** Every icon a catalog entry may name, by its lucide kebab-case name. An explicit map keeps the bundle small. */
const ICONS: Record<string, LucideIcon> = {
  'bar-chart': BarChart3, 'bar-chart-3': BarChart3, 'book-open': BookOpen, bot: Bot, box: Box, brain: Brain,
  briefcase: Briefcase, calendar: Calendar, cloud: Cloud, code: Code, 'credit-card': CreditCard, database: Database,
  'file-text': FileText, figma: Figma, folder: Folder, github: Github, gitlab: Gitlab, globe: Globe,
  'hard-drive': HardDrive, image: Image, link: Link, mail: Mail, 'map-pin': MapPin, 'message-square': MessageSquare,
  palette: Palette, search: Search, server: Server, slack: Slack, terminal: Terminal, wrench: Wrench,
  activity: Activity, bug: Bug, 'chart-line': ChartLine, 'check-square': CheckSquare, chrome: Chrome, clock: Clock,
  'git-branch': GitBranch, kanban: Kanban, layers: Layers, 'layout-dashboard': LayoutDashboard, 'list-checks': ListChecks,
  notebook: Notebook, receipt: Receipt, sparkles: Sparkles, table: Table, triangle: Triangle, wallet: Wallet, zap: Zap
}

export const iconFor = (name: string | undefined): LucideIcon => ICONS[name ?? ''] ?? Plug

/** Case-insensitive match on name, description, publisher and category; `category` '' or 'All' is no filter. */
export function filterCatalog(entries: McpCatalogEntry[], query: string, category: string): McpCatalogEntry[] {
  const q = query.trim().toLowerCase()
  return entries.filter((e) =>
    (!category || category === 'All' || e.category === category) &&
    (!q || [e.name, e.description, e.publisher, e.category, e.id].some((s) => s.toLowerCase().includes(q)))
  )
}

/** The values a form starts with: each field's default. */
export const initialValues = (entry: McpCatalogEntry): Record<string, string> =>
  Object.fromEntries(entry.fields.map((f) => [f.id, f.default ?? '']))

/** Ids of the required fields that are still blank. */
export const fieldsValid = (entry: McpCatalogEntry, values: Record<string, string>): string[] =>
  entry.fields.filter((f) => f.required && !(values[f.id] ?? '').trim()).map((f) => f.id)

/** One line to put under a card when the program that launches the connector is not installed; '' when fine. */
export function runtimeWarning(entry: Pick<McpCatalogEntry, 'runtime' | 'detected'>, runtimes: Record<string, { command: string; found: boolean; hint: string }>): string {
  if (entry.detected) return '' // the card's own detection line says it better
  const rt = runtimes[entry.runtime]
  return rt && !rt.found ? `${rt.command} not found. ${rt.hint}` : ''
}

/** What a card says about a locally detected program: found at a path, missing with a hint, or nothing to detect. */
export function detectionLabel(entry: Pick<McpCatalogEntry, 'detected'>): { found: boolean; text: string } | null {
  const d = entry.detected
  if (!d) return null
  return d.found ? { found: true, text: `Detected at ${d.path}` } : { found: false, text: d.hint }
}

/** Installs with one click: found on this Mac and nothing to fill in. */
export const oneClick = (entry: Pick<McpCatalogEntry, 'detected' | 'fields'>): boolean => !!entry.detected?.found && entry.fields.length === 0

/** The connector a tool belongs to: the server's own name when the event carries it, else read from `mcp__server__tool`. */
export function connectorName(toolName: string, mcp?: { server: string } | null): string {
  if (mcp?.server) return mcp.server
  const parts = toolName.split('__').filter(Boolean)
  return parts.length >= 3 && parts[0] === 'mcp' ? parts[1].replace(/[_-]+/g, ' ') : ''
}

/** The supervisor stops with this detail when only a browser sign-in can help; it is not a failure. */
export const needsSignIn = (s: McpServer): boolean => s.transport !== 'stdio' && s.live.status === 'error' && s.live.detail === 'sign-in required'

/** Split a pasted command line into argv, honouring simple quoting. */
export const tokenize = (line: string): string[] =>
  (line.match(/"[^"]*"|'[^']*'|\S+/g) ?? []).map((t) => t.replace(/^(['"])([\s\S]*)\1$/, '$2'))

/** The inverse, for showing a stored command back in one field. */
export const joinArgv = (command: string, args: string[]): string =>
  [command, ...args].filter(Boolean).map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(' ')

export const parseEnvText = (text: string): Record<string, string> =>
  Object.fromEntries(
    text
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'))
      .map((l) => {
        const i = l.indexOf('=')
        return i < 0 ? [l, ''] : [l.slice(0, i).trim(), l.slice(i + 1).trim()]
      })
      .filter(([k]) => k)
  )

export const envToText = (env: Record<string, string>): string =>
  Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')

/** What "Use this" on a registry hit fills into the custom add form. Keys go in empty: the user supplies the values. */
export function registryDraft(r: McpRegistryResult): {
  name: string; transport: 'stdio' | 'http' | 'sse'; argv: string; url: string
  envText: string; secretsText: string; headerRows: { k: string; v: string }[]; description: string
} {
  const remote = r.transport !== 'stdio'
  const plain = r.env_keys.filter((k) => !r.secret_keys.includes(k))
  return {
    name: r.name,
    transport: r.transport,
    argv: remote ? '' : joinArgv(r.install.command, r.install.args),
    url: remote ? r.install.url : '',
    // A remote hit's secret names are header names; a local one's are environment variables.
    envText: remote ? '' : plain.map((k) => `${k}=`).join('\n'),
    secretsText: remote ? '' : r.secret_keys.map((k) => `${k}=`).join('\n'),
    headerRows: remote ? r.secret_keys.map((k) => ({ k, v: '' })) : [],
    description: r.description
  }
}
