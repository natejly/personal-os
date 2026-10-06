import { Menu, type BrowserWindow } from 'electron'
import { contextMenuTemplate, type ContextMenuActions } from './contextMenu'

/** Pop the menu on a window's right-click; used by the main window and by pop-outs. */
export function attachContextMenu(win: BrowserWindow, verbs?: ContextMenuActions['verbs']): void {
  win.webContents.on('context-menu', (_e, params) => {
    if (win.isDestroyed()) return
    const items = contextMenuTemplate(params, {
      replaceMisspelling: (s) => win.webContents.replaceMisspelling(s),
      addToDictionary: (w) => win.webContents.session.addWordToSpellCheckerDictionary(w),
      verbs
    })
    if (items.length) Menu.buildFromTemplate(items).popup({ window: win })
  })
}
