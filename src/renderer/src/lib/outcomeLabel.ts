import type { MessageOutcome } from '@shared/types'

const LABELS: Record<MessageOutcome, string> = {
  stopped: 'Stopped',
  rounds: 'Stopped at the tool-round limit',
  tokens: 'Stopped at the token budget',
  time: 'Stopped at the time limit',
  cost: 'Stopped at the cost limit',
  loop: 'Stopped after repeating the same step',
  interrupted: 'Interrupted when the app closed',
  length: 'Cut off at the model’s output limit. Send "continue" to pick up where it stopped.',
  incomplete: 'The connection ended before the reply finished.'
}

/** The line under a reply that did not end normally; null for a complete one or an outcome this build does not know. */
export function outcomeLabel(outcome: string | null | undefined): string | null {
  return outcome && Object.prototype.hasOwnProperty.call(LABELS, outcome) ? LABELS[outcome as MessageOutcome] : null
}
