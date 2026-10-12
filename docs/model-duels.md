# Model duels (developer mode)

Settings > Advanced > Developer > Developer tools adds an **A/B** toggle under the composer. With it on, the next
messages go to two models at once. Each model does the **whole turn**: every round, every tool call, the final answer.
You compare the finished results, pick one, and the turn goes on from the winner. Votes feed Settings > Model > Model
duels (wins, losses, Elo, mean latency per model).

Cost doubles while a duel runs: both sides pay for every round, and the turn is not finished until both are.

## What each side may do

Each side works in a hidden copy of the chat (same history, same settings, never listed, deleted afterwards). Every tool
call it makes is judged in one place (`duel.Side.gate`):

| Call | During the duel |
| --- | --- |
| Reads: search, read a file or document, web fetch, calendar and mail reads, memory and graph search | Runs for real, on both sides |
| Files written in the desk folder or the chat's outbox (`desk_write_file`, `fs_edit`, `fs_copy`, `fs_mkdir`, `run_python` outputs, converted documents) | Runs, but into the side's **overlay** |
| `shell_run` | Runs in the OS sandbox with writes confined to the overlay and a scratch folder. Held when the sandbox is missing, open network is on, or `unsandboxed` is asked for |
| Everything else: mail, calendar and task writes, docs, memory, todos, connectors that may write, workers and delegation, coding sessions, the shared browser, `send_files`, a file write outside the overlay | **Held**: not run. The model is told the action is queued and carries on |

A held call is kept on the side as a pending action and listed under **Will do if picked**. Nothing a side does can reach
the outside world before the pick.

## The overlay

A side's writable folders are private copies under `<data>/tmp/ab/<message id>/<side>/`: a copy of the desk folder (desks)
and of the chat's outbox. The workspace resolves its folders through the overlay (`workspace.OVERLAY`), a path argument
that names the real folder is rewritten to the copy and rewritten back in the result, so a side reads its own writes
first and the real files otherwise. The copy is a full copy made when the side starts (a very large desk folder makes a
duel start slowly); it is not an overlay by path.

Each column lists the files its side made (open one to read its text) and shows its tool calls as ordinary tool cards.

## Picking

- **A is better / B is better**: the winner's files move into the real folders (and are recorded as the chat's files), the
  loser's copy is deleted, the winner's text and tool cards become the reply, and its held actions run for real, one by
  one, under the usual permission rules (Allow everything runs them; an always-ask tool such as `gmail_send` still shows
  its card). The turn then continues as usual, so an autonomous desk keeps going.
- **Regenerate** (the circular arrow on one side) deletes that side's copy and runs its whole turn again.
- **Stop**, an error on both sides, or closing the app deletes both copies. Leftovers are swept at startup.

## Limits

A shell command only reads real files it can reach; it cannot write them. A big tool result a side stored under a handle
(`read_tool_result`) is not readable from the real chat after the pick. The shared browser is not available to a side.
Chats without a desk have no folder for the shell, so a side's shell there only has a scratch directory.
