import { contextBridge, ipcRenderer } from 'electron'
import type { PersonalOSApi } from '../shared/types'

const api: PersonalOSApi = {
  backendUrl: () => ipcRenderer.invoke('backend:url'),
  backendStatus: () => ipcRenderer.invoke('backend:status'),
  backendToken: () => ipcRenderer.invoke('backend:token'),
  platform: process.platform,
  onMenu: (cb) => {
    const handler = (_e: Electron.IpcRendererEvent, action: string): void => cb(action)
    ipcRenderer.on('menu', handler)
    return () => ipcRenderer.removeListener('menu', handler)
  }
}

contextBridge.exposeInMainWorld('os', api)
