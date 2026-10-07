import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { ShowItem } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { uploadShowItem } from '../lib/showPanel'
import FileView, { UploadActions } from './FileView'

/**
 * A file opened with no chat beside it: an upload (Files → Uploads, a project file no chat used) by `id`, or any
 * ready file item (a chat's output from Files → Artifacts) by `item`, with that caller's header `actions` and `onClose`.
 * The same viewer as the side panel, in a dialog. The upload one is mounted once; the store holds which upload.
 */
export default function UploadPreview({ id, item: given, actions, onClose: close }: { id?: string; item?: ShowItem; actions?: JSX.Element; onClose?: () => void }): JSX.Element {
  const closeUpload = useStore((s) => s.openUploadPreview)
  const toast = useStore((s) => s.toast)
  const [loaded, setLoaded] = useState<ShowItem | null>(null)
  const onClose = (): void => { close ? close() : closeUpload(null) }
  useEffect(() => {
    if (!id) return
    let live = true
    setLoaded(null)
    api.documents.get(id).then((d) => { if (live) setLoaded(uploadShowItem(d)) }).catch((e: Error) => { toast(e.message, 'error'); closeUpload(null) })
    return () => { live = false }
  }, [id, closeUpload, toast])
  const item = given ?? loaded
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() } }}>
      <div className="modal upload-preview" role="dialog" aria-label={item?.title ?? 'File'} onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <h2>{item?.title ?? 'Loading…'}</h2>
          <span className="show-actions">
            {item && (actions ?? <UploadActions item={item} />)}
            <button autoFocus className="icon-btn" aria-label="Close file" onClick={onClose}><X size={16} /></button>
          </span>
        </header>
        <div className="show-body">{item && <FileView item={item} />}</div>
      </div>
    </div>
  )
}
