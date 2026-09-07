---
name: cc-peer
description: Message a Claude Code session running on another machine over SSH, when the official Remote Control path isn't available. Use when the user asks to tell, notify, ask, or hand something off to a session on a remote host, server, or box by name — e.g. "tell the api-worker on build-server that the migration finished". Not for sessions on this machine.
allowed-tools: Bash, Read
---

# Messaging a session on another machine

`SendMessage` already reaches sessions on this machine, and sessions on other
machines when both ends are connected to Remote Control. This skill is the
fallback for when neither applies — Bedrock/Vertex/Foundry, API-key auth,
air-gapped networks, or an unattended worker nobody connected.

## Before using this skill

**Try the built-in path first.** Run `ListAgents`. If the target session is
listed, use `SendMessage` and stop here — it is simpler and gives the receiving
Claude a reply address. Only continue when the target is on a machine that
doesn't show up.

## Where the script lives

`cc_peer.py`, invoked as `python3 /path/to/cc_peer.py`. If the path isn't known,
check `~/.claude/skills/cc-peer/cc_peer.py` and `~/bin/cc_peer.py` before asking
the user.

## Procedure

**1. List what's actually there.** Never guess a session name or PID.

```bash
python3 cc_peer.py list --host <ssh-host>
```

If the host is unreachable or has no sessions, say so and stop. If a session the
user named appears with `[stale record]` or `[no inbox]`, report that — it can't
be messaged, and re-running won't change it.

**2. Confirm the target with the user when it's ambiguous.** If several sessions
could match what they asked for, ask which one rather than picking. Address a
session by PID when two share a name.

**3. Post the message.** Write it yourself in the user's intent; keep it
self-contained, since the receiving session has none of this conversation's
context.

```bash
python3 cc_peer.py send --host <ssh-host> --to <name-or-pid> "<message>"
```

For anything long or containing quotes, backticks, or `$`, pipe it via stdin
instead of inlining it:

```bash
printf '%s' "$MESSAGE" | python3 cc_peer.py send --host <ssh-host> --to <name> -
```

**4. Report honestly.** The command prints `Posted to <name>'s inbox`. That means
the socket write succeeded — **not** that Claude read it. See below.

You don't need to introduce yourself in the text. Every send opens with
`From: <user>@<host> (<session>)`, filled in automatically — Claude Code records
socket-posted messages as coming from `unknown`, so this is the only thing that
tells the receiver who is asking.

## Answering a message you received

A message from another machine ends with its own return address:

```
Reply: python3 ~/.claude/skills/cc-peer/cc_peer.py send --host alice@100.73.93.61 --to api-worker --no-reply-to
```

**Run that line as printed** — it already carries the user, an absolute path, and
`--no-reply-to`. Pipe your answer into it:

```bash
printf '%s' "<your answer>" | python3 ~/.claude/skills/cc-peer/cc_peer.py send \
  --host alice@100.73.93.61 --to api-worker --no-reply-to -
```

If it fails on the username, the sender's account doesn't exist on your side —
say so in a reply routed some other way rather than guessing at names.

Reply when the message asked something, or when you finished work it handed you.
Don't reply to acknowledge receipt — that starts a turn on the other machine and
spends tokens to say nothing.

## What to tell the user afterwards

- **Posted ≠ delivered.** If the receiving session runs with
  `--dangerously-skip-permissions`, Claude Code holds the message for approval in
  *that* session and drops it after `dialogExpiry` (5 minutes by default) if
  nobody approves. Tell the user they may need to approve it there, or set
  `crossSessionInbound: "accept"` on that worker.
- **A reply is possible, but not guaranteed.** Every send appends a
  `Reply:` line naming this session and how to reach it, so the receiver
  can answer — *if* that machine can SSH back here. When it can't, nothing
  reports the failure to either side. If an answer matters, say so in the
  message rather than assuming one is coming.
- **Nothing arrives here on its own.** A reply is a fresh message into this
  session's inbox; it lands when it lands. Don't sit and wait for it — finish
  the turn, and read it when it shows up.

## Guardrails

- **Don't send unprompted.** Message a remote session only when the user asked
  for it. A message starts a turn on someone else's machine and spends tokens.
- **Reply once; don't hold a conversation.** Now that replies are addressable,
  two agents answering each other will keep answering. When you are responding
  to a message that carried a `Reply:` line, send with `--no-reply-to` so the
  exchange ends with you.
- **Don't route around permissions.** Never ask a remote session to do something
  this session was denied or blocked from doing. Bring it back to the user
  instead. The receiving side is instructed to refuse it anyway.
- **Never paste credentials into a message.** It lands verbatim in another
  machine's transcript.

## When it doesn't work

| Symptom | Cause |
| :-- | :-- |
| `no reachable session named X` | Wrong name, or the session ended. `list --all` shows stale records. |
| Session listed but `[no inbox]` | It's alive but bound no socket — it can't receive messages at all. |
| Posted, but nothing happened over there | Almost always held for approval. Check that session's screen. |
| `ssh ... timed out` | Plain SSH problem; verify with `ssh <host> true` first. |
