import type { ExtensionInstallRequest } from '@shared/types'
import { useState } from 'react'
import { installRequest } from '../../lib/extensionsCatalog'
import './extensionsSection.css'

/** Install from a git URL, a zip URL or a folder on this machine. Fetching only downloads into quarantine; the consent sheet comes next. */
export default function ExtensionsSectionInstall({ busy, onFetch }: {
  busy: boolean; onFetch: (req: ExtensionInstallRequest) => void
}): JSX.Element {
  const [text, setText] = useState('')
  const [ref, setRef] = useState('')
  const [subdir, setSubdir] = useState('')
  const [error, setError] = useState<string | null>(null)
  const submit = (): void => {
    const req = installRequest(text, ref, subdir)
    if ('error' in req) { setError(req.error); return }
    setError(null)
    onFetch(req)
  }
  return (
    <form className="ext-install" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <div className="ext-install-row">
        <input
          type="text" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false}
          aria-label="Git URL, zip URL or absolute folder path" placeholder="https://github.com/you/extension, a .zip URL, or /path/to/folder"
        />
        <button type="submit" className="primary-btn" disabled={busy || !text.trim()}>{busy ? 'Fetching...' : 'Fetch'}</button>
      </div>
      <details className="ext-install-more">
        <summary>Branch, tag or subfolder</summary>
        <div className="ext-install-row">
          <input type="text" value={ref} onChange={(e) => setRef(e.target.value)} aria-label="Branch, tag or commit" placeholder="Branch, tag or commit" spellCheck={false} />
          <input type="text" value={subdir} onChange={(e) => setSubdir(e.target.value)} aria-label="Subfolder" placeholder="Subfolder" spellCheck={false} />
        </div>
      </details>
      {error && <p className="ext-note error" role="alert">{error}</p>}
    </form>
  )
}
