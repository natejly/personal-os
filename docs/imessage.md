# Texting Grain (iMessage)

Text Grain's agent from your iPhone. The backend watches the Messages database on your Mac,
treats a text from an allowlisted number or email as a normal chat turn, and replies in the same
Messages thread. Tools, permissions and approvals work exactly as they do in the app: a text never
skips an approval and never turns on skip-permissions.

It is off until you turn it on, and it only works while your Mac is awake, Messages is signed in,
and Grain is running.

---

## Setup

1. **Messages on the Mac, with its own Apple ID.** Grain only acts on texts the Mac *receives*.
   If the Mac's Messages is signed into the same Apple ID as your iPhone, everything you send shows
   up there as your own message and Grain ignores it. So sign Messages on the Mac into a separate
   Apple ID (a spare one, or one made for Grain), then text that Apple ID's email from your iPhone.
   Start a thread from the iPhone and check that the message appears on the Mac.
2. **Full Disk Access.** The Messages database (`~/Library/Messages/chat.db`) is protected.
   System Settings → Privacy & Security → Full Disk Access → turn on **Grain**.
   When running from source (`./scripts/dev.sh`), grant it to the app that launches the backend
   instead: your terminal (Terminal, or whichever you use) and, if it is listed separately,
   Electron. Quit and reopen the app after granting.
   Settings → Integrations → Texting has an **Open Privacy settings** button and a **Re-check**.
3. **Automation.** The first time Grain sends a reply, macOS asks whether Grain may control
   Messages. Allow it. If you denied it: System Settings → Privacy & Security → Automation →
   Grain → turn on **Messages**.
4. **Grain settings** (Settings → Integrations → Texting (iMessage)):
   - **Allowed numbers and emails**: your iPhone's number and Apple ID email, the ones the Mac
     sees your texts come from. Phone numbers are stored as `+15551234567` (ten-digit numbers are
     taken as US); emails are lowercased. Add both, since the iPhone may send from either.
   - **Conversation**: where texts land. The default is a dedicated chat called **Texts**,
     created on first use.
   - Turn the switch on, press **Save**, then **Send test text**. You should get
     "Grain is connected ✅" on your phone.

Turning it on starts from the newest message in the database: nothing you sent before is
replayed. After a backend restart Grain picks up where it left off, but ignores texts older than
ten minutes.

## Commands

A command is the whole message (case does not matter). Anything else is a normal chat turn.

| Text | Does |
| --- | --- |
| `status` | What is running, how long the Texts run has been going, how many approvals wait |
| `stop` | Stops the reply running in the Texts conversation |
| `new` | Starts a fresh Texts conversation and sends later texts there |
| `yes` / `y` / `approve` | Approves the one pending approval |
| `no` / `n` / `deny` | Denies it |
| `yes 34` / `no 34` | Answers approval number 34 (the number in its text) |
| `help` | Lists the commands |

If a reply is still running when you text again, the text is folded into that reply (like typing
into the composer mid-reply) instead of starting a second one.

### Approvals by text

When a run in the Texts conversation needs approval, Grain texts the tool, a one-line summary and
the key arguments (secrets removed), then "Reply yes or no". With more than one waiting, each gets
a number and a bare yes/no is refused. The number is always needed (`yes 34`) for calls Grain marks
as needing extra confirmation and for calls whose arguments had to be shortened to fit a text. A
"yes please" while an approval waits is not taken as an answer; reply exactly yes or no. Plans and
questions from the agent are not answered by text: Grain tells you one is waiting in the app. Answers go through the same approval path as the card in the
app, so rules, history and audit entries are unchanged; the history notes that the answer came by
text.

### Replies

Replies are plain text: markdown is stripped and links become bare URLs. Long replies are split
into numbered parts of about 1,500 characters; past four parts the rest is left in Grain
("…full reply in Grain"). If a run fails, the text says so and the details stay in Grain.

### Long runs (optional)

**Also text me when long runs finish or need approval** (off by default) texts a one-line note
when a run started in the app takes longer than the set minutes (default 3), and texts approval
requests from any run, not only the Texts conversation.

## Privacy and safety

- Only allowlisted handles are acted on. Texts from anyone else get no reply and start nothing;
  Grain only counts them ("3 ignored, last 2h ago"). Group chats are ignored.
- Only iMessage counts. SMS sender numbers can be faked, so forwarded SMS are ignored.
- Messages sent from the Mac's account (`is_from_me`) are never treated as input, and a text that
  repeats one of Grain's recent replies is dropped, so Grain cannot answer itself.
- Tapbacks and reactions are skipped. Attachments are not supported yet; an attachment-only text
  gets a short reply saying so.
- The Messages database is opened read-only, one short query every few seconds; Grain never
  writes to it or holds it open.
- Reply text reaches Messages as an argument to a fixed script, never as script source.
- Logs carry masked handles (last four digits), row numbers, lengths and outcomes. Message bodies
  stay out of logs.
- Rate limits: 10 texts a minute per sender (one "slow down" reply, then silence; `stop` and `no`
  always get through) and paced outgoing sends.
- Only one Grain backend on the Mac watches Messages at a time (a per-user lock tied to the
  Messages database), so a dev backend running beside the app does not double-reply.

## Limits

- Needs the Mac awake, Messages signed in and Grain running. Texts sent while Grain is down for more
  than ten minutes are skipped.
- iMessage only, in both directions.
- No attachments, images or voice notes in either direction.
- Approvals that need edited arguments, or plans you want to edit step by step, still need the app.
