/**
 * When to restart a backend that died: at most `max` unexpected exits inside a sliding `windowMs`, each
 * restart delayed by a growing backoff. Pure, so the schedule is testable without a child process.
 */
export const MAX_RESTARTS = 5
export const WINDOW_MS = 5 * 60_000
export const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 15_000]

export class RestartPolicy {
  private exits: number[] = []

  constructor(
    private readonly max = MAX_RESTARTS,
    private readonly windowMs = WINDOW_MS,
    private readonly backoff = BACKOFF_MS
  ) {}

  /** Record an unexpected exit at `now`. Returns the delay before the next try, or null to give up. */
  next(now: number): number | null {
    this.exits = this.exits.filter((t) => now - t < this.windowMs)
    this.exits.push(now)
    if (this.exits.length > this.max) return null
    return this.backoff[Math.min(this.exits.length - 1, this.backoff.length - 1)]
  }

  /** Forget the history: a restart the user asked for gets a clean slate. */
  reset(): void {
    this.exits = []
  }

  get recent(): number {
    return this.exits.length
  }
}
