# Texting Grain (Telegram)

Message Grain from your phone through a private Telegram bot that only you can use. The backend
long-polls the bot, treats a message in your chat with it as a normal chat turn, and replies there.
Tools, permissions and approvals work exactly as they do in the app: a message never skips an
approval and never turns on skip-permissions.

It is off until you add a bot token and turn it on, and it only works while your Mac is awake and
Grain is running. The bot is yours: you create it, you hold its token, and nobody else is accepted.

---

## Setup

1. **Create a bot.** In Telegram, open **@BotFather** and send `/newbot`. Pick a name, then a
   username that ends in `bot`. BotFather replies with a token.
2. **Paste the token** in Settings → Texting and press **Save token**. Grain checks it with
   Telegram, turns texting on and shows a pairing code.
3. **Pair your chat.** Press **Open in Telegram** (or open the link yourself) and press **Start**.
   You can also send `/start <code>` to the bot by hand. Grain replies "Paired." and the status
   line shows your name.
4. **Send test message.** "Grain is connected." should appear in the chat.

The code works once and expires after 15 minutes. **New code** issues another. Replacing the token
with a different bot's clears the pairing and issues a fresh code.

Turning it on starts from now: messages sent while Grain was off are not replayed. After a restart
Grain picks up where it left off but ignores messages older than ten minutes.

## Commands

| Message | Does |
| --- | --- |
| `/status` | What is running, how long the Texts run has been going, how many approvals wait |
| `/stop` | Stops the reply running in the Texts conversation |
| `/new` | Starts a fresh Texts conversation and sends later messages there |
| `/help` | Lists the commands |

Anything else is a normal chat turn. If a reply is still running when you write again, the message
is folded into that reply (like typing into the composer mid-reply) instead of starting a second
one. Grain shows "typing" in Telegram while a run you started there is live.

### Approvals

When a run in the Texts conversation needs approval, Grain sends the tool, a one-line summary and
the key arguments (secrets removed) with **Approve** and **Deny** buttons. Tapping one answers, and
the message is edited to show "Approved." or "Denied." with the buttons gone.

You can also reply `yes` or `no` (also `y`, `approve`, `n`, `deny`). With more than one waiting,
each carries a code in brackets and a bare yes/no is refused; answer with the code (`yes 34`). The
code is always needed for calls Grain marks as needing extra confirmation, for calls whose
arguments had to be shortened to fit a message, and for a text answer. A button tap is an explicit
choice, so it needs no code. Plans and questions from the agent are not answered here: Grain says
one is waiting in the app.

Answers go through the same approval path as the card in the app, so rules, history and audit
entries are unchanged; the history notes that the answer came by Telegram.

### Replies

Replies are plain text: markdown is stripped and links become bare URLs. Long replies are split at
paragraph breaks into messages under Telegram's 4,096 character limit; past about eight parts the
rest is left in Grain ("…full reply in Grain"). If a run fails, the message says so and the
details stay in Grain.

### Long runs (optional)

**Message me when long runs finish or need approval** (off by default) sends "Grain finished:
<title> (N min)" when a chat run started in the app takes at least the set minutes (default 3), and
sends approval requests from any run, not only the Texts conversation.

## Privacy and safety

- **Owner only.** Grain accepts one private chat with one Telegram account. Messages from anyone
  else, groups, channels, edited messages and button taps from other accounts are ignored without
  a reply. Before pairing, the only thing accepted is `/start <code>` with the right, unexpired
  code.
- **Pairing code.** Eight random characters, single use, valid for 15 minutes, compared in constant
  time. A wrong or expired code gets no reply.
- **Token storage.** The bot token lives in the macOS Keychain, with a file readable only by you
  (mode 0600) when the Keychain is unavailable. It is never a setting, so it never appears in the
  settings API, backups of settings, or the UI after saving. It is also kept out of logs and error
  messages.
- **Logs** carry masked ids, lengths and outcomes. Message bodies stay out of logs.
- **Rate limit.** 20 messages a minute from you (one "Slow down" reply, then silence; `/stop` and
  deny always get through).
- **One poller per token per Mac.** A per-user lock tied to the token means a dev backend running
  beside the app does not double-reply.
- Telegram's bot messages are not end-to-end encrypted: Telegram's servers see what you and the
  bot exchange. Keep secrets out of the conversation.

## Troubleshooting

The status line in Settings → Texting names the problem.

| Status | Meaning and fix |
| --- | --- |
| No bot token yet | Paste a token from @BotFather and press Save token. |
| Off | The switch is off. Turn it on. |
| Not paired | Open the link or send `/start <code>` to the bot. A used or expired code needs **New code**. |
| Telegram rejected the token | The token is wrong or was revoked. Paste a new one (BotFather `/token`). |
| Another program is reading this bot's updates, or a webhook is set | Telegram allows one reader per bot. Stop the other program, or clear the webhook, and Grain retries every 30 seconds. |
| Another Grain is polling this bot | Another Grain backend on this Mac holds the lock for the token. Quit it, or use a separate bot per backend. |
| Polling error | A network or Telegram failure. Grain retries with a growing delay up to a minute; press Re-check. |

## Limits

- Needs the Mac awake and Grain running. Messages sent while Grain is down for more than ten
  minutes are skipped.
- Private chat with one owner only; no group use.
- No attachments, images or voice notes in either direction.
- Approvals that need edited arguments, or plans you want to edit step by step, still need the app.
