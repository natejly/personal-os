import { Sigma } from '../../icons'
import { useStore } from '../../store'
import type { UiApi } from '../host'
import LatexDocEditor from './LatexDocEditor'
import { latexApi } from './api'

const isLatexSource = (title: string): boolean => /\.(tex|bib|sty|cls)$/i.test(title.trim())

/** A Files folder with a main.tex, opened on its main file. */
async function newProject(scope: string): Promise<void> {
  const s = useStore.getState()
  try {
    const p = await latexApi.create('LaTeX project', scope || null)
    await Promise.all([s.refreshDocs(), s.refreshDocFolders()])
    s.expandTo(p.scope, p.folder)
    await s.openDoc(p.main_doc_id)
  } catch (e) { s.toast((e as Error).message, 'error') }
}

export function activate(ui: UiApi): void {
  ui.docEditor({ id: 'latex', match: (doc) => isLatexSource(doc.title), Component: LatexDocEditor })
  ui.slot('files.new', { id: 'latex', order: 0, label: 'New LaTeX project', icon: <Sigma size={13} />, run: (scope) => void newProject(scope) })
}
