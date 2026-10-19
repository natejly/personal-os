# History digest

The History digest is a built-in extension (`memory-tree`, off until you turn it on in Settings > Advanced > Extensions) that keeps a bounded digest of everything you have told the assistant: recent memories word for word, older ones summarised, the older the shorter. It adds a third view over your memories. It does not replace the memory list, the knowledge graph or relevance search, and it never changes a memory.

## What it keeps

Every memory becomes one leaf in an append-only log: one line, at most 280 bytes, starting with its date. There is one log for the personal scope and one for each project, and a project's leaves never appear in the personal digest or in another project's. The log is a cache over your memories and can be rebuilt at any time from the live ones (`POST /memory-tree/rebuild`), in the order they became true.

- A new memory is appended. Changing a memory's text retires its old leaf and appends a new one; forgetting, superseding or trashing a memory retires its leaf. A retired leaf keeps its place in the log but is left out of every summary and digest.
- Memories learned automatically can be left out (setting below).
- A memory learned in a chat that read someone else's text (a web page, an email) is marked as unverified. It is still in the digest, shown with an `[unverified]` marker and given to the summarising model as a claim, not a fact.

## Merges

The log is covered by aligned blocks of 2, 4, 8, ... leaves. A block gets a one-line summary once it is full. Blocks of up to 16 leaves are summarised from their leaves; larger ones from the summaries of their two halves. The smallest, oldest block is done first.

A cheap model makes the summaries in the background: a few after each reply (`Merges per reply`), and eight at a time every ten minutes. They never hold up a chat. The model is told to keep what has lasting effect, drop what does not, invent nothing, and to treat the entries as data, not instructions. Each summary is cut to one line of 280 bytes and has credentials removed. If the model fails, the block stays pending and the worker waits, longer after each failure, before trying again.

Each summary is stored with a hash of what it read. When a leaf is retired, every summary above it is flagged out of date; it stays in the digest until it has been made again, and a flag whose inputs turn out unchanged is cleared without a model call.

## The digest

When the History digest memory system is selected (Settings > Runtime, for everything, a project or a chat), a turn gets one extra block, "History digest", after the built-in memory blocks. The chat's memory switch still applies: with memory off nothing is added.

The block is fitted to `Digest tokens` (1500 by default). Every leaf is shown word for word when they fit. Otherwise the digest is a set of blocks that tiles the log: a block is kept whole when its size is at most alpha times its age (how far its newest leaf is from the newest leaf of the log), and alpha is searched for the smallest value that fits. Spare budget is then spent splitting the blocks nearest the present first. Recent memories are therefore exact and old ones coarse. Leaves that are not yet in a full block are shown as they are. A block without a summary yet is shown as its halves, and when even the coarsest cover does not fit the oldest lines are dropped. A chat in a project gets two sections, the personal one and the project's, sharing the budget. The same log always gives the same digest.

Lines look like `[8-12) 2026-03-01..2026-03-09: summary`. The range is what `memory_zoom` takes.

## Zoom and search

`memory_zoom` opens a block into its two halves (single memories at the bottom), so the assistant can look closer at a coarse line. It only takes an aligned block the digest could show; it answers a bad range with an example. `memory_tree_status` reports, per scope, how many leaves, retired leaves, summaries, out-of-date summaries and pending merges there are, and when the last merge ran.

Once some summaries exist, the extension also adds a retrieval source: it ranks summaries by BM25 against the message, opens the best one into its halves, and offers up to six lines with a pointer to the block to zoom.

## Settings

- Digest tokens: 300 to 6000, default 1500.
- Merges per reply: 0 to 10, default 2. Zero leaves the merging to the ten-minute job.
- Merge model: the fast model (default) or the default model.
- Include automatic memories: on by default.

## The memory system slot

`memory-tree/digest` is a provider of the `memory.system` slot that layers on the built-in memory. `learn`, `profile`, `remember`, `forget` and `search` are the built-in ones; `recall` returns the built-in blocks plus the digest; `status` adds the tree counts. If it fails, the built-in memory serves the call and a card says so, as for any provider. See [runtime slots](runtime-slots.md).

## Limits

- It only sees memories written while it is on. Turn it on, then rebuild each scope once to file the earlier ones.
- Summaries lose detail on purpose; the memory list stays the source of truth, and a forgotten memory disappears from the digest at once but from its summaries only when they are made again.
- The log is read in full for each scope when it changes, which is fine for thousands of memories and not designed for millions.
- It answers "what has happened over time", not "what do I know about X". Relevance search is unchanged.
