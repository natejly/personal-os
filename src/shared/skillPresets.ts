/**
 * Popular skills published in the open-source SKILL.md format (agentskills.io), offered under Library → Skills →
 * Popular skills and as import hints in the composer. Nothing here is bundled: an import fetches the file from its
 * repository (backend skillmd.raw_url turns the page URL into the raw file) and lands as a candidate the user
 * approves. Bundled scripts/ and assets/ are never imported, so a skill that leans on them is left out.
 *
 * `chars` is the body size when the list was checked (2026-10-05); past what fits the model's window a chat reads a skill on
 * demand instead of carrying it in every prompt.
 */
export interface SkillPreset {
  /** The skill's own name, which is also the folder name and the `/skill` slug after import. */
  name: string
  /** One line in plain words about what it does. */
  blurb: string
  /** The repository page of the skill's folder. */
  url: string
  /** owner/repo */
  source: string
  license: string
  kind: 'work' | 'thinking' | 'coding'
  chars: number
}

export const PRESET_KIND_LABEL: Record<SkillPreset['kind'], string> = {
  work: 'Work and writing',
  thinking: 'Thinking and planning',
  coding: 'Coding'
}

export const SKILL_PRESETS: SkillPreset[] = [
  // ---- work and writing
  { name: 'meeting-insights-analyzer', kind: 'work', chars: 9793, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/meeting-insights-analyzer',
    blurb: 'Reads a meeting transcript for how you communicated: filler words, avoided conflict, talking over people, missed chances to listen.' },
  { name: 'file-organizer', kind: 'work', chars: 10793, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/file-organizer',
    blurb: 'Tidies folders by what the files are: finds duplicates, proposes a structure, and names the cleanup steps.' },
  { name: 'invoice-organizer', kind: 'work', chars: 11313, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/invoice-organizer',
    blurb: 'Sorts invoices and receipts for tax time: reads each file, pulls out the key fields, renames consistently, files by period.' },
  { name: 'changelog-generator', kind: 'work', chars: 2765, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/changelog-generator',
    blurb: 'Turns a commit history into release notes a customer can read, grouped by kind of change.' },
  { name: 'tailored-resume-generator', kind: 'work', chars: 12205, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/tailored-resume-generator',
    blurb: 'Reads a job description and reshapes a résumé around the experience and skills it asks for.' },
  { name: 'content-research-writer', kind: 'work', chars: 13850, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/content-research-writer',
    blurb: 'Writes long-form pieces with you: research with citations, sharper hooks, outline passes, feedback section by section.' },
  { name: 'internal-comms', kind: 'work', chars: 1098, license: 'Apache-2.0', source: 'anthropics/skills',
    url: 'https://github.com/anthropics/skills/tree/main/skills/internal-comms',
    blurb: 'Formats for internal updates: status reports, leadership updates, newsletters, FAQs, incident write-ups.' },
  { name: 'doc-coauthoring', kind: 'work', chars: 15341, license: 'Apache-2.0', source: 'anthropics/skills',
    url: 'https://github.com/anthropics/skills/tree/main/skills/doc-coauthoring',
    blurb: 'A three-stage way to write a proposal, spec or decision doc together: hand over context, refine, then test it on a reader.' },
  { name: 'writing-guidelines', kind: 'work', chars: 896, license: 'MIT', source: 'vercel-labs/agent-skills',
    url: 'https://github.com/vercel-labs/agent-skills/tree/main/skills/writing-guidelines',
    blurb: 'Reviews prose against a published writing handbook: voice, tone, clarity. Fetches the current rules when it runs.' },
  { name: 'domain-name-brainstormer', kind: 'work', chars: 5429, license: 'Apache-2.0', source: 'ComposioHQ/awesome-claude-skills',
    url: 'https://github.com/ComposioHQ/awesome-claude-skills/tree/master/domain-name-brainstormer',
    blurb: 'Brainstorms names for a project and checks which domains are free across the common endings.' },

  // ---- thinking and planning
  { name: 'grill-me', kind: 'thinking', chars: 36, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/productivity/grill-me',
    blurb: 'A relentless interview that sharpens a plan or design before you commit to it.' },
  { name: 'brainstorming', kind: 'thinking', chars: 17214, license: 'MIT', source: 'obra/superpowers',
    url: 'https://github.com/obra/superpowers/tree/main/skills/brainstorming',
    blurb: 'Explores intent, requirements and design before any creative work, with a hard stop for your approval.' },
  { name: 'to-questionnaire', kind: 'thinking', chars: 2736, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/productivity/to-questionnaire',
    blurb: 'Turns a decision you cannot settle alone into a questionnaire for the person who can.' },
  { name: 'wait-what', kind: 'thinking', chars: 273, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/productivity/wait-what',
    blurb: 'When the last reply did not land: stop and pitch it again, differently.' },
  { name: 'research', kind: 'thinking', chars: 517, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/engineering/research',
    blurb: 'Investigates a question against primary sources and writes the findings up as a file you keep.' },
  { name: 'retro', kind: 'thinking', chars: 4256, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/engineering/retro',
    blurb: 'Runs a retrospective on a working session: what went well, what did not, what to change.' },
  { name: 'handoff', kind: 'thinking', chars: 682, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff',
    blurb: 'Compacts the conversation into a handoff document the next agent or person can pick up.' },
  { name: 'writing-plans', kind: 'thinking', chars: 10189, license: 'MIT', source: 'obra/superpowers',
    url: 'https://github.com/obra/superpowers/tree/main/skills/writing-plans',
    blurb: 'Turns a spec into a step-by-step plan with a check after every step, before anything is built.' },

  // ---- coding
  { name: 'tdd', kind: 'coding', chars: 3359, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd',
    blurb: 'Test-first at the seams you have confirmed, with integration tests as tracer bullets.' },
  { name: 'test-driven-development', kind: 'coding', chars: 9423, license: 'MIT', source: 'obra/superpowers',
    url: 'https://github.com/obra/superpowers/tree/main/skills/test-driven-development',
    blurb: 'Red, green, refactor: write the failing test first and watch it fail before any implementation.' },
  { name: 'systematic-debugging', kind: 'coding', chars: 9299, license: 'MIT', source: 'obra/superpowers',
    url: 'https://github.com/obra/superpowers/tree/main/skills/systematic-debugging',
    blurb: 'Four phases to the root cause before any fix; three failed fixes means question the design.' },
  { name: 'verification-before-completion', kind: 'coding', chars: 3312, license: 'MIT', source: 'obra/superpowers',
    url: 'https://github.com/obra/superpowers/tree/main/skills/verification-before-completion',
    blurb: 'No "done", "fixed" or "passing" without a fresh run that shows it. Evidence before claims.' },
  { name: 'code-review', kind: 'coding', chars: 6096, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/engineering/code-review',
    blurb: 'Reviews changes since a fixed point on two axes: the repo\'s own standards, and the spec that asked for them.' },
  { name: 'triage', kind: 'coding', chars: 6354, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/engineering/triage',
    blurb: 'Moves issues and outside pull requests through triage roles: categorise, verify, question, brief.' },
  { name: 'to-spec', kind: 'coding', chars: 2823, license: 'MIT', source: 'mattpocock/skills',
    url: 'https://github.com/mattpocock/skills/tree/main/skills/engineering/to-spec',
    blurb: 'Turns what the conversation already settled into a spec and files it, with no further interview.' },
  { name: 'frontend-design', kind: 'coding', chars: 9074, license: 'Apache-2.0', source: 'anthropics/skills',
    url: 'https://github.com/anthropics/skills/tree/main/skills/frontend-design',
    blurb: 'Distinctive, intentional UI: aesthetic direction, typography, and choices that do not read as templated defaults.' },
  { name: 'web-design-guidelines', kind: 'coding', chars: 914, license: 'MIT', source: 'vercel-labs/agent-skills',
    url: 'https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines',
    blurb: 'Audits UI code against a published list of accessibility and interface rules, fetched when it runs.' },
  { name: 'vercel-react-best-practices', kind: 'coding', chars: 6805, license: 'MIT', source: 'vercel-labs/agent-skills',
    url: 'https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices',
    blurb: 'Performance rules for React and Next.js code in priority order, for writing, reviewing or refactoring.' },
  { name: 'mcp-builder', kind: 'coding', chars: 8701, license: 'Apache-2.0', source: 'anthropics/skills',
    url: 'https://github.com/anthropics/skills/tree/main/skills/mcp-builder',
    blurb: 'How to build a good MCP server around an external API, in Python or TypeScript, with evals.' }
]
