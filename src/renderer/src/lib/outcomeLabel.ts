import type { MessageOutcome } from '@shared/types'

const LABELS: Record<MessageOutcome, string> = {
  stopped: 'Stopped',
  // Older versions stopped replies at a limit; those rows keep their outcome.
  rounds: 'Stopped early',
  tokens: 'Stopped early',
  time: 'Stopped early',
  cost: 'Stopped early',
  loop: 'Stopped after repeating the same step',
  interrupted: 'Interrupted when the app closed',
  length: 'Cut off at the model’s output limit. Send "continue" to pick up where it stopped.',
  incomplete: 'The connection ended before the reply finished.'
}

/** The line under a reply that did not end normally; null for a complete one or an outcome this build does not know. */
export function outcomeLabel(outcome: string | null | undefined): string | null {
  return outcome && Object.prototype.hasOwnProperty.call(LABELS, outcome) ? LABELS[outcome as MessageOutcome] : null
}
