/** "3 days left" until the automatic purge erases a trashed item. `purgeAt` is epoch seconds. */
export const daysLeft = (purgeAt: number, nowMs = Date.now()): string => {
  const d = Math.ceil((purgeAt * 1000 - nowMs) / 86_400_000)
  return d <= 0 ? 'purging soon' : `${d} day${d === 1 ? '' : 's'} left`
}
