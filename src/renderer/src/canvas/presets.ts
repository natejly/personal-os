import { create } from 'zustand'
import type { CanvasPreset } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { flushViewport, useCanvas } from './store'

/**
 * Space presets: named templates of a space's windows and layout. Their own small store, loaded on
 * demand (every time a presets popover opens), since nothing else on screen shows them.
 */
interface PresetsState {
  presets: CanvasPreset[]
  loaded: boolean
  load: () => Promise<void>
  save: (canvasId: string, name: string) => Promise<CanvasPreset | null>
  rename: (id: string, name: string) => Promise<void>
  remove: (id: string) => Promise<void>
  /** Instantiate → new space; adoptSpace() navigates to it. Toasts when windows were skipped. */
  instantiate: (id: string, name?: string) => Promise<void>
}

const fail = (e: unknown): void => useStore.getState().toast((e as Error)?.message ?? String(e), 'error')

export const usePresets = create<PresetsState>((set, get) => ({
  presets: [],
  loaded: false,

  load: async () => {
    try {
      set({ presets: await api.presets.list(), loaded: true })
    } catch (e) {
      fail(e)
    }
  },

  save: async (canvasId, name) => {
    try {
      // The server snapshots its own rows, so land any debounced (or earlier failed) layout and
      // pan/zoom writes first, or the preset records the geometry from before them.
      await Promise.all([useCanvas.getState().flushLayout(), flushViewport(canvasId)])
      const p = await api.presets.create({ canvas_id: canvasId, name: name.trim() })
      set((s) => ({ presets: [...s.presets.filter((x) => x.id !== p.id), p] }))
      useStore.getState().toast(`Saved preset "${p.name}"`, 'info')
      return p
    } catch (e) {
      fail(e)
      return null
    }
  },

  rename: async (id, name) => {
    const next = name.trim()
    const prev = get().presets.find((p) => p.id === id)
    if (!prev || !next || next === prev.name) return
    set((s) => ({ presets: s.presets.map((p) => (p.id === id ? { ...p, name: next } : p)) }))
    try {
      const p = await api.presets.update(id, { name: next })
      set((s) => ({ presets: s.presets.map((x) => (x.id === id ? p : x)) }))
    } catch (e) {
      set((s) => ({ presets: s.presets.map((p) => (p.id === id ? { ...p, name: prev.name } : p)) }))
      fail(e)
    }
  },

  remove: async (id) => {
    set((s) => ({ presets: s.presets.filter((p) => p.id !== id) }))
    try {
      await api.presets.delete(id)
    } catch (e) {
      fail(e)
      await get().load()
    }
  },

  instantiate: async (id, name) => {
    try {
      const { skipped, ...canvas } = await api.presets.instantiate(id, name ? { name } : {})
      useCanvas.getState().adoptSpace(canvas)
      if (skipped) useStore.getState().toast(`${skipped} window${skipped === 1 ? '' : 's'} skipped: their content no longer exists`, 'info')
    } catch (e) {
      fail(e)
    }
  }
}))
