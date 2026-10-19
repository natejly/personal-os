import { useMemo } from 'react'
import { sproutLook } from '../lib/sprout'
import '../styles/sprout.css'

export type SproutMood = 'idle' | 'thinking' | 'unsure' | 'surprised' | 'scared' | 'sad' | 'sleepy' | 'happy'

/** Below this pixel size the creases, highlight and thin outlines go, so the mark stays legible. */
const SMALL_PX = 20
const EYE = '#2b2112'
const LEAF_L = 'M0 -9Q-4 -20 -17 -20Q-16 -9 0 -9Z'
const LEAF_R = 'M0 -9Q4 -20 17 -20Q16 -9 0 -9Z'

/**
 * The mascot: a grain with dot eyes and a two-leaf sprout, drawn from the name (see lib/sprout.ts).
 * Motion is CSS on `data-mood` (styles/sprout.css): it runs while `live` or hovered, otherwise the pose holds still.
 * `hue` (OKLCH degrees) and `tone` (pale 0 to ink 1) lock the colour so a family differs by shape alone.
 */
export default function Sprout({ name, mood = 'idle', size, live, title, hue, tone, className }: { name: string; mood?: SproutMood; size?: number; live?: boolean; title?: string; hue?: number; tone?: number; className?: string }): JSX.Element {
  const look = useMemo(() => sproutLook(name, { hue, tone }), [name, hue, tone])
  const small = size !== undefined && size < SMALL_PX
  const cls = ['sprout', live && 'sprout-live', small && 'sprout-small', className].filter(Boolean).join(' ')
  const { gap } = look
  const r = small ? 6.5 : 5
  const lash = small ? 5 : 4
  return (
    <svg className={cls} width={size} height={size} viewBox="0 0 100 100" data-mood={mood} role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      {title && <title>{title}</title>}
      <g className="sp-fx">
        <g className="sp-bob">
          <g transform={`translate(${look.cx} ${look.cy}) rotate(${look.tilt})`}>
            <ellipse rx={look.rx} ry={look.ry} fill={look.fill} stroke={small ? 'none' : look.stroke} strokeWidth={3} />
            {!small && (
              <>
                <path d="M0 10Q-3 18 0 26" fill="none" stroke={look.stroke} strokeWidth={2} strokeLinecap="round" opacity={0.3} />
                <ellipse cx={-10} cy={-14} rx={2.6} ry={6} transform="rotate(14 -10 -14)" fill="#fff6e0" opacity={0.8} />
              </>
            )}
            <g className="sp-eyes">
              {mood === 'sleepy' ? (
                <path d={`M${-gap - 5} -2H${-gap + 5}M${gap - 5} -2H${gap + 5}`} fill="none" stroke={EYE} strokeWidth={lash} strokeLinecap="round" />
              ) : (
                <>
                  <circle cx={-gap} cy={mood === 'sad' ? 3 : -2} r={mood === 'unsure' ? r * 0.8 : r} fill={EYE} />
                  <circle cx={gap} cy={mood === 'sad' ? 3 : -2} r={mood === 'unsure' ? r * 1.1 : r} fill={EYE} />
                </>
              )}
            </g>
          </g>
          <g transform={`translate(${look.tipX} ${look.tipY})${look.leaf === 1 ? '' : ` scale(${look.leaf})`}`}>
            <g className="sp-sway">
              <path className="sp-stem" d="M0 3V-9" fill="none" strokeWidth={small ? 5 : 3} strokeLinecap="round" />
              <g className="sp-leaf sp-leaf-l" strokeLinejoin="round"><path d={LEAF_L} /></g>
              <g className="sp-leaf sp-leaf-r" strokeLinejoin="round"><path d={LEAF_R} /></g>
              {mood === 'happy' && (
                <g className="sp-bud"><circle cx={0} cy={-13} r={3.8} fill="#e9c46a" stroke={small ? 'none' : '#8a5a2b'} strokeWidth={1.2} /></g>
              )}
            </g>
          </g>
        </g>
      </g>
    </svg>
  )
}
