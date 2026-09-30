import { contextBridge, ipcRenderer } from 'electron'
import type { BusMessage, GrainApi, PopoutChange, PopoutOpenRequest, ShortcutState } from '../shared/types'

/** Subscribe to a main->renderer channel, returning an unsubscribe function. */
function listen<T>(channel: string, cb: (payload: T) => void): () => void {
  const handler = (_e: Electron.IpcRendererEvent, payload: T): void => cb(payload)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const api: GrainApi = {
  backendUrl: () => ipcRenderer.invoke('backend:url'),
  backendStatus: () => ipcRenderer.invoke('backend:status'),
  backendToken: () => ipcRenderer.invoke('backend:token'),
  platform: process.platform,
  onMenu: (cb) => listen<string>('menu', cb),
  popout: {
    open: (windowId: string, req?: PopoutOpenRequest) => ipcRenderer.invoke('popout:open', windowId, req ?? {}),
    close: (windowId: string) => ipcRenderer.invoke('popout:close', windowId),
    focus: (windowId: string) => ipcRenderer.invoke('popout:focus', windowId),
    setPinned: (windowId: string, pinned: boolean) => ipcRenderer.invoke('popout:set-pinned', windowId, pinned),
    setMinSize: (windowId: string, minWidth: number, minHeight: number) =>
      ipcRenderer.invoke('popout:set-min-size', windowId, minWidth, minHeight),
    list: () => ipcRenderer.invoke('popout:list'),
    gather: () => ipcRenderer.invoke('popout:gather'),
    scatter: () => ipcRenderer.invoke('popout:scatter'),
    onChanged: (cb) => listen<PopoutChange>('popout:changed', cb)
  },
  bus: {
    send: (msg: BusMessage) => ipcRenderer.send('bus', msg),
    on: (cb) => listen<BusMessage>('bus', cb)
  },
  shortcuts: {
    gather: () => ipcRenderer.invoke('shortcuts:gather'),
    setGather: (accelerator: string) => ipcRenderer.invoke('shortcuts:set-gather', accelerator),
    onFailure: (cb) => listen<ShortcutState>('shortcuts:failed', cb)
  },
  closeSelf: () => ipcRenderer.send('window:close-self'),
  minimizeSelf: () => ipcRenderer.send('window:minimize-self')
}

contextBridge.exposeInMainWorld('os', api)
