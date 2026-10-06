import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { ShowItem } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { uploadShowItem } from '../lib/showPanel'
import FileView, { UploadActions } from './FileView'

/** An upload opened with no chat beside it (Files → Uploads, a project file no chat used): the same viewer as the side panel, in a dialog. Mounted once; the store holds which upload. */
export default function UploadPreview({ id }: { id: string }): JSX.Element {
  const close = useStore((s) => s.openUploadPreview)
  const toast = useStore((s) => s.toast)
  const [item, setItem] = useState<ShowItem | null>(null)
  useEffect(() => {
    let live = true
    setItem(null)
    api.documents.get(id).then((d) => { if (live) setItem(uploadShowItem(d)) }).catch((e: Error) => { toast(e.message, 'error'); close(null) })
    return () => { live = false }
  }, [id, close, toast])
  const onClose = (): void => close(null)
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() } }}>
      <div className="modal upload-preview" role="dialog" aria-label={item?.title ?? 'File'} onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <h2>{item?.title ?? 'Loading…'}</h2>
          <span className="show-actions">
            {item && <UploadActions item={item} />}
            <button autoFocus className="icon-btn" aria-label="Close file" onClick={onClose}><X size={16} /></button>
          </span>
        </header>
        <div className="show-body">{item && <FileView item={item} />}</div>
      </div>
    </div>
  )
}
