# When and why to extract memories (sources behind learn.py)

Research notes, 2026-10-05. The extractor prompt in `backend/personal_os/learn.py` and its friction → skill
path are built on these. This folder is the one place in the repo that names other products.

## What production systems keep

- **LangMem** splits memory into semantic (facts or a profile), episodic (successful interactions kept as worked
  examples) and procedural (behavioural rules that evolve from feedback). It also separates hot-path extraction
  from a background pass, which is where cross-turn patterns such as friction are mined.
  https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- **Claude Code auto-memory** saves four kinds of note: who the user is, *feedback* (corrections they give and
  approaches they confirm), project state not derivable from code or git, and references. Corrections are the
  trigger: when the user corrects it, it decides whether the point will matter later and writes it down. It
  skips anything derivable from the codebase or already in CLAUDE.md. https://code.claude.com/docs/en/memory
- **ChatGPT memory** keeps preferences, interests and goals as a visible editable list and tells the user when
  one is saved. It is trained not to proactively remember sensitive details (health and the like) unless asked.
  https://openai.com/index/memory-and-new-controls-for-chatgpt/
- **Anthropic memory tool** is meant for lessons from past interactions, decisions and feedback. The docs
  recommend code-side stripping of sensitive data, size caps and expiry.
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- **Mem0** runs two phases: extract salient facts from the latest exchange plus context, then retrieve the most
  similar existing memories and choose ADD, UPDATE, DELETE or NOOP. Novelty against existing rows is a built-in
  gate. https://arxiv.org/abs/2504.19413
- **Zep / Graphiti** keep a temporal graph: facts get validity timestamps instead of being overwritten.
  https://arxiv.org/abs/2501.13956
- **Voyager** admits a skill into its library only after a self-verification gate; **Reflexion** stores verbal
  self-critiques as episodic memory. https://arxiv.org/html/2305.16291

## Corrections and friction

- Claude Code records both corrections and *confirmed* approaches, which stops the store learning only negatives.
- LangMem's prompt optimiser turns trajectories plus user feedback into revised instructions (the example: a
  user disliking theory-first answers produces "lead with practical examples").
- Anthropic's skill best-practices guide says to "notice what information you repeatedly provide" and capture it
  as a skill. Repeated re-explanation is the signal that a procedure should exist.
  https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- The skill-creator skill extracts the workflow from the conversation history first, and asks: what should be
  done, when should it trigger, what output is expected. Its loop is draft → test on a few realistic prompts
  against a baseline → human review → improve, generalising from patterns rather than patching one case, and
  removing instructions that are not pulling their weight.
  https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md

## What learn.py does with this

- Memory types the prompt names: identity, durable preferences, feedback (corrections *and* confirmed
  approaches, written as standing guidance with scope and reason), goals and decisions, pointers.
- Filters: a month's durability, not derivable from connected data, not already stored (updates instead),
  stated beats inferred (an inferred preference must have shown twice and say so), sensitive categories only
  on an explicit "remember", never credentials.
- Friction is reported once per exchange with a fix: a *preference* lands as a memory; a *procedure* names the
  repeatable task, and the worker drafts one candidate skill from it (`skillbuild.draft_skill`) with the
  friction as its rationale. One per chat, never a near-duplicate of an existing skill, approved only by hand.
- Approved skills in the prompt are listed to the extractor, which reports worked or failed. A failure drafts a
  revised copy beside the live row (`revise_skill`), linted by the approval gate, with the observed failure as
  its rationale. The live text is never edited by the model that followed it.
