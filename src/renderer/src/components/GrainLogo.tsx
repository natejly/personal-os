/**
 * The Grain logo: a rice-grain clipart. Fixed golden colors (not currentColor) so the brand mark
 * reads the same on light and dark chrome. Master artwork lives in build/grain.svg; the app icon
 * (build/icon.png) and tray silhouette are generated from the same shape by scripts/make-icons.py.
 */
export default function GrainLogo({ size = 15 }: { size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <g transform="rotate(35 12 12)">
        <ellipse cx="12" cy="12" rx="4.8" ry="8.6" fill="#e9c46a" stroke="#8a5a2b" strokeWidth="1.4" />
        <path d="M12 4.6 C 10.3 7.4 10.3 16.6 12 19.4" fill="none" stroke="#8a5a2b" strokeWidth="1.1" strokeLinecap="round" opacity="0.55" />
        <path d="M14.6 6.4 C 15.7 8.2 15.9 10.2 15.5 12.4" fill="none" stroke="#fff6e0" strokeWidth="1.3" strokeLinecap="round" opacity="0.9" />
      </g>
    </svg>
  )
}
