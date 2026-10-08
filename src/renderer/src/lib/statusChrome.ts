/**
 * What a chat shows about work in progress. The main agent gets no status chrome of its own: no strip, no card, no
 * checklist row, no turn or round line. The composer's Stop is its control and the reply itself is its progress.
 * Status chrome only appears while background workers are running, and it is about them (the workers card), never the
 * main agent: there is no strip above the composer, even while workers run.
 */

/** The workers card above the composer: only while one is live. Finished workers are listed in the side panel. */
export const workersCardShown = (liveWorkers: number): boolean => liveWorkers > 0

/** A plan in the transcript only while it waits for the user's approval (that is a question, not a status). Once approved
 *  its checklist lives in the side panel. */
export const inlinePlanShown = (plan: { status: string } | null | undefined): boolean => plan?.status === 'pending'
