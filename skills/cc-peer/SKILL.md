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

## What to tell the user afterwards

- **Posted ≠ delivered.** If the receiving session runs with
  `--dangerously-skip-permissions`, Claude Code holds the message for approval in
  *that* session and drops it after `dialogExpiry` (5 minutes by default) if
  nobody approves. Tell the user they may need to approve it there, or set
  `crossSessionInbound: "accept"` on that worker.
- **It's one-way.** The receiving Claude has no reply address and cannot answer.
  If the user wants a response, either check that session's transcript, or ask
  them to have it post back with `cc-peer` in the other direction.

## Guardrails

- **Don't send unprompted.** Message a remote session only when the user asked
  for it. A message starts a turn on someone else's machine and spends tokens.
- **One message, not a conversation.** Don't poll or follow up in a loop waiting
  for a reaction; there is no reply channel to wait on.
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
