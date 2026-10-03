/** A run of summary lines; `ids` is set when its last line cites transcript segments. */
export interface EvidenceChunk { text: string; ids: string[] }

/**
 * Split a summary body into chunks that each end at a cited line, so a source button can sit after
 * it. `evidence` maps a line index of `body` to segment ids. With none, the body is one chunk.
 * ponytail: a list split across chunks renders as separate lists, so an ordered list restarts its numbers.
 */
export function evidenceChunks(body: string, evidence: Record<string, string[]> | null | undefined): EvidenceChunk[] {
  const chunks: EvidenceChunk[] = []
  let cur: string[] = []
  body.split('\n').forEach((line, i) => {
    cur.push(line)
    const ids = evidence?.[String(i)]
    if (ids && ids.length) {
      chunks.push({ text: cur.join('\n'), ids })
      cur = []
    }
  })
  if (cur.length) chunks.push({ text: cur.join('\n'), ids: [] })
  return chunks
}
