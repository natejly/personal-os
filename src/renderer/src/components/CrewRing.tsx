import Face from './Face'

export interface RingNode { id: string; name?: string; status?: string; title: string }

/** Where child i of n sits, in % of the frame: evenly round a ring, the first straight up. */
export const ringPoints = (n: number, radius = 38): { x: number; y: number }[] =>
  Array.from({ length: n }, (_, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n
    return { x: 50 + radius * Math.cos(a), y: 50 + radius * Math.sin(a) }
  })

/**
 * One agent in the middle, its subagents as small faces on a ring with a line each. Sized in % so it follows
 * its frame. With no children it is just the centre face.
 */
export default function CrewRing({ center, kids, onPick, onCenter }: {
  center: { name: string; hue?: number; status?: string; title: string }
  kids: RingNode[]
  onPick: (id: string) => void
  onCenter?: () => void
}): JSX.Element {
  const face = <Face name={center.name} hue={center.hue} status={center.status} size="fill" title={center.title} />
  if (kids.length === 0) return face
  const pts = ringPoints(kids.length)
  const stop = (e: { stopPropagation: () => void }): void => e.stopPropagation()
  return (
    <span className="crew-ring">
      <svg className="crew-lines" viewBox="0 0 100 100" aria-hidden>
        {pts.map((p, i) => <line key={kids[i].id} x1={50} y1={50} x2={p.x} y2={p.y} />)}
      </svg>
      {onCenter
        ? <button className="crew-center crew-sat-btn" title={center.title} onPointerDown={stop} onClick={(e) => { stop(e); onCenter() }}>{face}</button>
        : <span className="crew-center">{face}</span>}
      {kids.map((k, i) => (
        <button key={k.id} className="crew-sat" style={{ left: `${pts[i].x}%`, top: `${pts[i].y}%` }} title={k.title}
          onPointerDown={stop} onClick={(e) => { stop(e); onPick(k.id) }}>
          <Face name={k.name ?? k.id} status={k.status} size="fill" />
        </button>
      ))}
    </span>
  )
}
