/** Save `data` as a .json download (the renderer has no native save dialog for plain files). */
export function downloadJson(name: string, data: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** Open the native file picker and parse the chosen .json file; null when cancelled. Rejects on a file that is not JSON. */
export function pickJson(): Promise<unknown | null> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'application/json,.json'
    input.onchange = async () => {
      const f = input.files?.[0]
      if (!f) return resolve(null)
      try { resolve(JSON.parse(await f.text())) } catch { reject(new Error('That file is not valid JSON.')) }
    }
    input.oncancel = () => resolve(null)
    input.click()
  })
}
