import { GRAIN_FILLS, GRAIN_RX, GRAIN_RY, waveDelays } from '../lib/kernel'
import { GRAIN_STROKE } from '../lib/sprout'
import '../styles/sprout.css'

/** Beat between neighbouring grains, in ms; the loop itself is set in sprout.css. */
const STAGGER_MS = 120
const DELAYS = waveDelays(GRAIN_FILLS.length, STAGGER_MS)
/** Resting lean of each grain, in degrees. */
const LEANS = [-12, 6, -4]

/**
 * A thinking indicator that sits in a line of text: three small grains in a row, each bobbing and tilting a beat after
 * the one before. About 1em tall, so it scales with the font size; the text beside it carries the meaning. Still under reduced motion.
 */
export default function GrainWave({ className }: { className?: string }): JSX.Element {
  return (
    <svg className={['grain-wave', className].filter(Boolean).join(' ')} viewBox="0 0 36 16" aria-hidden="true">
      {GRAIN_FILLS.map((fill, i) => (
        <g key={i} transform={`translate(${6 + i * 12} 8) rotate(${LEANS[i]})`}>
          <g className="gw-grain" style={{ animationDelay: `${DELAYS[i]}ms` }}>
            <ellipse rx={GRAIN_RX / 2.6} ry={GRAIN_RY / 2.6} fill={fill} stroke={GRAIN_STROKE} strokeWidth={0.8} />
          </g>
        </g>
      ))}
    </svg>
  )
}
