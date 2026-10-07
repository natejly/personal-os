/** A small paper plane in a circle: the Telegram chat's mark in the sidebar and the chat header. */
export default function TelegramIcon({ size = 14 }: { size?: number }): JSX.Element {
  // Decorative: it always sits beside the visible "Telegram" label, so a screen reader must not read it twice.
  return (
    <svg data-testid="telegram-icon" aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <path d="M17.5 7.5 6.5 11.8l3.6 1.5 1.5 3.6 2.2-2.6 2.8 2.1z" fill="currentColor" stroke="none" />
    </svg>
  )
}
