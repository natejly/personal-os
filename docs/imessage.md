# Texting Grain (iMessage)

Text yourself from your iPhone and Grain answers in that same thread. The backend watches the
Messages database on your Mac, treats a message in your note-to-self conversation as a normal chat
turn, and replies there. Tools, permissions and approvals work exactly as they do in the app: a
text never skips an approval and never turns on skip-permissions.

It uses the Apple ID you already have. The Mac's Messages app stays signed into your own account;
there is no second account to set up.

It is off until you turn it on, and it only works while your Mac is awake, Messages is signed in,
and Grain is running.

---

## Setup

1. **Messages on the Mac, signed into your Apple ID** (the same one as your iPhone). Check that a
   message you send yourself from the iPhone shows up in Messages on the Mac.
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
   - **Your addresses**: your own phone number and Apple ID email. Phone numbers are stored as
     `+15551234567` (ten-digit numbers are taken as US); emails are lowercased.
   - Turn the switch on and press **Save**.
   - **Self chat**: press **Detect**. Grain lists the one-to-one iMessage conversations with one of
     your own addresses, with the last few digits and the last activity (never the messages). Pick
     yours and press **Save**. If nothing shows up, text yourself once from the iPhone and press
     Detect again. Until you pick one, no conversation is treated as your self chat.
   - **Send test text**. "Grain is connected ✅" should appear in the conversation with yourself.
5. **On the iPhone**, open the conversation with yourself (your own number or email) and text
   commands or anything you would type into Grain.

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

## Not answering itself

Grain's replies land in the same conversation as your texts, and both show up on the Mac as sent
by you. Several guards keep Grain from reading its own replies as commands:

- **Reply marker.** Every text Grain sends (replies, each part of a split reply, approval requests,
  status, test and long-run notes) starts with a marker, `🌾 ` by default (Settings → Reply
  marker; a blank marker falls back to the default). Any self-chat message starting with the
  marker is skipped, so do not start your own texts with it.
- **Sent ledger.** Before each send, Grain records a fingerprint of the text (a hash, never the
  text) with the conversation and time; once the message shows up in the Messages database, its
  row is recorded too. A self-chat message that matches a recorded row, or whose fingerprint
  matches something Grain sent in the last ten minutes, is skipped. The record is written before
  the send starts, so a reply that lands before Grain finishes looking for it is still caught.
  Entries older than a day are dropped.
- **One text, one turn.** If the same text from you appears twice (once as sent, once as
  received), only the first is taken.
- **Loop guard.** The usual rate limit applies to the self chat. If more than five self-chat
  messages arrive back to back within a minute, Grain pauses texting, stops sending and shows
  "Paused" in Settings. The pause survives a restart of Grain; turn texting off and on to resume.

## Other people

Other numbers and emails in **Your addresses** still work the way they always did: a text they
send from their own conversation with you is taken as a turn and answered there. In every
conversation except your self chat, messages sent from your account are ignored.

## Privacy and safety

- Only your self chat and the listed addresses are acted on. Texts from anyone else get no reply
  and start nothing; Grain only counts them ("3 ignored, last 2h ago"). Grain's own replies coming
  back are skipped without being counted. Group chats are ignored
  unless the group itself is listed.
- Only iMessage counts. SMS sender numbers can be faked, so forwarded SMS are ignored.
- Messages sent from your account count only in the self chat you picked, minus Grain's own
  replies (see above).
- Tapbacks and reactions are skipped. Attachments are not supported yet; an attachment-only text
  gets a short reply saying so.
- The Messages database is opened read-only, one short query every few seconds; Grain never
  writes to it or holds it open. Detecting the self chat reads conversation ids and dates, never
  message text.
- Reply text reaches Messages as an argument to a fixed script, never as script source.
- Logs carry masked handles (last four digits), row numbers, lengths and outcomes. Message bodies
  stay out of logs, and the sent ledger keeps hashes only.
- Rate limits: 10 texts a minute per sender (one "slow down" reply, then silence; `stop` and `no`
  always get through) and paced outgoing sends.
- Only one Grain backend on the Mac watches Messages at a time (a per-user lock tied to the
  Messages database), so a dev backend running beside the app does not double-reply.

## Limits

- Needs the Mac awake, Messages signed in and Grain running. Texts sent while Grain is down for more
  than ten minutes are skipped.
- iMessage only, in both directions.
- Grain's replies come from your own account, so the iPhone shows them as sent by you and may not
  notify you about them. Keep the conversation open, or check it, to see a reply.
- Change the marker only while no reply is on its way. A reply already sent with the old marker is
  caught once its row is recorded (a few seconds after it lands); before that, only the default
  marker is still recognised.
- No attachments, images or voice notes in either direction.
- Approvals that need edited arguments, or plans you want to edit step by step, still need the app.
