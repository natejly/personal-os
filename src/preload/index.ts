import { contextBridge, ipcRenderer } from 'electron'
import type { AgentBrowserFrame, BackendInfo, BusMessage, GrainApi, PopoutChange, PopoutOpenRequest, ShortcutState } from '../shared/types'

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
  backendInfo: () => ipcRenderer.invoke('backend:info'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  onBackendState: (cb) => listen<BackendInfo>('backend:state', cb),
  openLogs: () => ipcRenderer.invoke('backend:open-logs'),
  platform: process.platform,
  onMenu: (cb) => listen<string>('menu', cb),
  popout: {
    open: (windowId: string, req?: PopoutOpenRequest) => ipcRenderer.invoke('popout:open', windowId, req ?? {}),
    close: (windowId: string) => ipcRenderer.invoke('popout:close', windowId),
    focus: (windowId: string) => ipcRenderer.invoke('popout:focus', windowId),
    setPinned: (windowId: string, pinned: boolean) => ipcRenderer.invoke('popout:set-pinned', windowId, pinned),
    setOpacity: (windowId: string, opacity: number) => ipcRenderer.invoke('popout:set-opacity', windowId, opacity),
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
    capture: () => ipcRenderer.invoke('shortcuts:capture'),
    setCapture: (accelerator: string) => ipcRenderer.invoke('shortcuts:set-capture', accelerator),
    onFailure: (cb) => listen<ShortcutState>('shortcuts:failed', cb)
  },
  data: {
    chooseExportPath: () => ipcRenderer.invoke('data:choose-export-path'),
    chooseInputFiles: () => ipcRenderer.invoke('data:choose-input-files'),
    chooseFolder: () => ipcRenderer.invoke('data:choose-folder'),
    reveal: (path: string) => ipcRenderer.invoke('data:reveal', path),
    relaunch: () => ipcRenderer.invoke('data:relaunch')
  },
  print: {
    /** The hidden print window asks for its note, then says it has finished drawing it. */
    payload: () => ipcRenderer.invoke('print:payload'),
    ready: () => ipcRenderer.send('print:ready'),
    /** Print a note to PDF: 'save' asks where (and reveals the file), 'bytes' hands the PDF back. null when cancelled. */
    exportPdf: (title: string, content: string, filename: string, mode: 'save' | 'bytes') => ipcRenderer.invoke('print:export-pdf', title, content, filename, mode)
  },
  closeSelf: () => ipcRenderer.send('window:close-self'),
  minimizeSelf: () => ipcRenderer.send('window:minimize-self'),
  deskNotify: (payload) => ipcRenderer.send('desk:notify', payload),
  micAccess: () => ipcRenderer.invoke('media:mic-access'),
  agentBrowser: {
    list: () => ipcRenderer.invoke('agentBrowser:list'),
    show: (session: string) => ipcRenderer.invoke('agentBrowser:show', session),
    hide: (session: string) => ipcRenderer.invoke('agentBrowser:hide', session),
    signIns: () => ipcRenderer.invoke('agentBrowser:signIns'),
    clearSignIns: (domain?: string) => ipcRenderer.invoke('agentBrowser:clearSignIns', domain),
    subscribe: (session: string, cb) => {
      const off = listen<AgentBrowserFrame & { session: string }>('agentBrowser:frame', (f) => {
        if (f.session === session) cb({ dataUrl: f.dataUrl, url: f.url, title: f.title, at: f.at })
      })
      void ipcRenderer.invoke('agentBrowser:subscribe', session)
      return () => {
        off()
        void ipcRenderer.invoke('agentBrowser:unsubscribe', session)
      }
    }
  }
}

contextBridge.exposeInMainWorld('os', api)
