/** A timed block inside one day column, in minutes from midnight. */
export interface DaySpan {
  id: string
  startMin: number
  endMin: number
}

/** Which of `cols` side-by-side lanes a block is drawn in. */
export interface Lane {
  col: number
  cols: number
}

/**
 * Side-by-side lanes for one day's timed blocks, so events that overlap in time share the column's
 * width instead of painting over each other.
 *
 * Greedy: in start order (longer first on a tie), each block takes the first lane that is already
 * free. A cluster is a run of blocks with no gap in it; every block in a cluster is drawn at the
 * cluster's lane count, and clusters are independent, so one crowded hour does not narrow the rest
 * of the day. A block that ends exactly when the next starts does not overlap it.
 */
export function layoutDay(spans: DaySpan[]): Map<string, Lane> {
  const out = new Map<string, Lane>()
  const sorted = [...spans].sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin)
  let cluster: Lane[] = []
  let laneEnds: number[] = []
  let clusterEnd = -Infinity
  const close = (): void => {
    for (const lane of cluster) lane.cols = laneEnds.length
    cluster = []
    laneEnds = []
  }
  for (const s of sorted) {
    // A zero-length block still takes up a lane where it sits.
    const end = Math.max(s.endMin, s.startMin + 1)
    if (s.startMin >= clusterEnd) close()
    let col = laneEnds.findIndex((laneEnd) => laneEnd <= s.startMin)
    if (col === -1) col = laneEnds.length
    laneEnds[col] = end
    const lane = { col, cols: 1 }
    out.set(s.id, lane)
    cluster.push(lane)
    clusterEnd = Math.max(clusterEnd, end)
  }
  close()
  return out
}
