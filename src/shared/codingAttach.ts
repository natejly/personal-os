/** Claude Code session ids are 8 lowercase hex characters; anything else never reaches a script. */
export const isAttachId = (id: unknown): id is string => typeof id === 'string' && /^[0-9a-f]{8}$/.test(id)

/** The whole `.command` file: a login shell (so the user's PATH finds `claude`) and one attach line. */
export const attachScript = (id: string): string => `#!/bin/zsh -l\nclaude attach ${id}\n`
