import { GRAIN_RX, GRAIN_RY, KERNEL_CENTRE, kernelGrains } from '../lib/kernel'
import { GRAIN_STROKE } from '../lib/sprout'
import '../styles/sprout.css'

/** Below this pixel size the outline and highlight go, so the three grains stay legible. */
const SMALL_PX = 20
const GRAINS = kernelGrains()

/** A loading indicator: three grains in a small triangle, slowly orbiting their centre. Still under reduced motion. */
export default function KernelSpinner({ size = 16, className }: { size?: number; className?: string }): JSX.Element {
  const small = size < SMALL_PX
  return (
    <svg className={['kernel-spinner', className].filter(Boolean).join(' ')} width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <g className="ks-orbit" style={{ transformOrigin: `50px ${KERNEL_CENTRE}px` }}>
        {GRAINS.map((g, i) => (
          <g key={i} transform={`translate(${g.x} ${g.y}) rotate(${g.tilt})`}>
            <ellipse rx={GRAIN_RX} ry={GRAIN_RY} fill={g.fill} stroke={small ? 'none' : GRAIN_STROKE} strokeWidth={2.4} />
            {!small && <ellipse cx={-4} cy={-8} rx={1.6} ry={4.5} transform="rotate(14 -4 -8)" fill="#fff6e0" opacity={0.8} />}
          </g>
        ))}
      </g>
    </svg>
  )
}
