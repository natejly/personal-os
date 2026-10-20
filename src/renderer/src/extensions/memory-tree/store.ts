import { create } from 'zustand'
import { req } from '../../lib/api'
import { openReducer, type Cover, type Open, type OpenAction, type RebuildResult, type TreeNode, zoomKey } from './cover'

interface DigestState {
  covers: Record<string, Cover>
  /** Zoom results, by `scope|range`, so a block opened twice is fetched once. */
  zoomed: Record<string, TreeNode[]>
  open: Record<string, Open>
  loading: Record<string, boolean>
  error: string | null
  loadCover: (scope: string) => Promise<void>
  zoom: (scope: string, range: string) => Promise<void>
  toggle: (scope: string, range: string) => void
  rebuild: (scope: string) => Promise<RebuildResult>
}

const q = (s: string): string => encodeURIComponent(s)

export const useDigest = create<DigestState>((set, get) => ({
  covers: {}, zoomed: {}, open: {}, loading: {}, error: null,
  loadCover: async (scope) => {
    set((s) => ({ loading: { ...s.loading, [scope]: true } }))
    try {
      const cover = await req<Cover>(`/memory-tree/cover?scope=${q(scope)}`)
      // A rebuilt or grown tree can change what a range holds, so cached zooms for the scope are dropped.
      set((s) => ({
        covers: { ...s.covers, [scope]: cover },
        zoomed: Object.fromEntries(Object.entries(s.zoomed).filter(([k]) => !k.startsWith(`${scope}|`))),
        error: null
      }))
    } catch (e) {
      set({ error: (e as Error).message })
    } finally {
      set((s) => ({ loading: { ...s.loading, [scope]: false } }))
    }
  },
  zoom: async (scope, range) => {
    if (get().zoomed[zoomKey(scope, range)]) return
    try {
      const r = await req<{ range: string; children: TreeNode[] }>(`/memory-tree/zoom?range=${q(range)}&scope=${q(scope)}`)
      set((s) => ({ zoomed: { ...s.zoomed, [zoomKey(scope, range)]: r.children }, error: null }))
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },
  toggle: (scope, range) => {
    const action: OpenAction = { type: 'toggle', range }
    set((s) => ({ open: { ...s.open, [scope]: openReducer(s.open[scope] ?? {}, action) } }))
    if (get().open[scope]?.[range]) void get().zoom(scope, range)
  },
  rebuild: async (scope) => {
    const r = await req<RebuildResult>(`/memory-tree/rebuild?scope=${q(scope)}`, { method: 'POST' })
    set((s) => ({ open: { ...s.open, [scope]: {} } }))
    return r
  }
}))
