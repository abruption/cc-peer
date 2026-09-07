# cc-peer

Message a Claude Code session on **another machine over SSH**, without the message leaving your network.

```console
$ cc-peer list --host build-server
Sessions on build-server:
NAME             PID    STATUS  CWD
api-worker       4011   idle    /srv/api
migration-watch  4614   busy    /srv/api

$ cc-peer send --host build-server --to api-worker "Schema migration landed; rebase is safe now."
Posted to api-worker's inbox on build-server (44 chars).
```

## Use the official feature first

Claude Code has [cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging) built in, and it already covers most cases:

| Target | How the official feature delivers it |
| :-- | :-- |
| Same machine | Per-session Unix socket. Never touches Anthropic's servers. |
| Another machine | **Through Anthropic's servers**, over [Remote Control](https://code.claude.com/docs/en/remote-control). |
| Claude on the web | Through Anthropic's servers. |

If Remote Control works for you, **use it** — it needs no scripts and it gives the receiving Claude a reply address.

`cc-peer` is for the cases where it isn't available:

- **Bedrock / Vertex / Foundry** — Remote Control is disabled on those providers.
- **API-key auth** — finding sessions beyond this machine needs a claude.ai sign-in.
- **Air-gapped or compliance-bound networks** — where relaying through a third party is the problem.
- **Unattended workers** — a headless box nobody is around to connect to Remote Control.

If none of those describe you, you probably don't need this.

## How it works

Claude Code binds a Unix domain socket per session as that session's inbox, and documents it under [*The session's inbox socket*](https://code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket) — explicitly *"when you want a script or hook to post into a session"*. Posting is one JSON line:

```json
{"type":"user","message":{"role":"user","content":"your message"}}
```

That socket is local to its machine, and forwarding it doesn't help: Claude Code verifies the peer process and uid on the connection, so `ssh -L` gets refused as *"an endpoint that isn't the expected process"*.

So `cc-peer` doesn't forward the socket. It runs the same write **inside a remote shell**, where the connection is local again. `--host` pipes this script over SSH to `python3 -`, so nothing needs to be installed on the remote machine.

### Read the socket path; never guess it

Session records live in `~/.claude/sessions/<pid>.json` and carry the socket path. It is not always `/tmp/cc-socks/` — on two Ubuntu hosts running the same Claude Code build, one bound under `/tmp/cc-socks/` and the other under `/run/user/1001/cc-socks/`. Claude Code also falls back to a private per-user directory when it rejects the one it would have used. `cc-peer` reads `messagingSocketPath` out of the record and treats a live PID with no bound socket as unreachable.

## Install

One file, standard library only, Python 3.9+.

```bash
curl -O https://raw.githubusercontent.com/abruption/cc-peer/main/cc_peer.py
chmod +x cc_peer.py
```

The remote side needs `python3` and your SSH access. Nothing else.

## Usage

```bash
cc-peer list                                  # sessions on this machine
cc-peer list --host web-01                    # sessions over there
cc-peer list --host web-01 --all              # include stale records / no inbox

cc-peer send --to api-worker "message"        # local session
cc-peer send --host web-01 --to api-worker "message"
cc-peer send --host web-01 --to 4011 "message"          # address by pid
git log --oneline -5 | cc-peer send --host web-01 --to api-worker -   # stdin

cc-peer send --host web-01 --to api-worker --dry-run "x"   # resolve only
cc-peer list --host web-01 --json             # machine-readable
cc-peer send --host web-01 --ssh-opt -p --ssh-opt 2222 --to api-worker "..."
```

Exit codes: `0` posted, `1` error, `2` no such session.

## The receiving side decides what happens next

**"Posted" is not "delivered."** Writing to the socket succeeds; whether Claude ever reads the message is up to that session's [inbound controls](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages).

The default that surprises people: **a session running with `--dangerously-skip-permissions` holds every incoming message for its user's approval**, unless the sender identifies itself as also bypassing. A script can't make that claim, so on an unattended bypass-mode worker your message sits in an approval dialog and expires after `dialogExpiry` (5 minutes by default).

For a worker meant to take messages unattended, set this in its settings:

```json
{ "crossSessionInbound": "accept" }
```

Or start it with `--settings '{"crossSessionInbound":"accept"}'`. Do this deliberately: it means anything that can write to that socket can start a turn on that machine.

## Why not `tmux send-keys`?

`ssh host 'tmux send-keys -t sess "msg" Enter'` needs no script, and for a quick nudge it's fine. It breaks down as soon as timing or payload get interesting:

| | `tmux send-keys` | inbox socket |
| :-- | :-- | :-- |
| Session is mid-tool-call | Keystrokes land wherever focus is — possibly a subprocess's stdin | Queued, read at a turn boundary |
| Quotes, backticks, `$`, newlines, emoji | Shell and terminal both get a say | Delivered byte-for-byte in JSON |
| Rapid sends | Races | Native queue, burst limits, duplicate drop |
| Attribution | Looks like the user typed it | Arrives marked as another session, and can't approve permissions |

That last row matters: a message posted to the inbox [cannot answer a permission prompt or change configuration](https://code.claude.com/docs/en/cross-session-messaging#how-a-session-treats-an-incoming-message). Text typed via `send-keys` is indistinguishable from you.

## Limits

- **One-way.** Cross-machine messages sent outside Remote Control carry no reply address, so the receiving Claude can't answer. Point `cc-peer` back the other way if you need a round trip.
- **No discovery across a bastion.** `--host` is a single SSH hop; chain it yourself with an SSH config `ProxyJump`.
- **Same OS user.** The socket is restricted to the user that owns the session, so `cc-peer` gives you nothing you couldn't already do with your own shell on that host. It is not a privilege-escalation path — but it does mean anyone with that shell can start a turn.
- **Linux and macOS only.** Native Windows uses named pipes with a mandatory auth line; unsupported here.

## Verified

Claude Code **v2.1.263**, macOS 15 (Apple silicon) and Ubuntu 24.04 (arm64), over Tailscale SSH.

The session record schema (`~/.claude/sessions/*.json`) is not part of Claude Code's documented interface and can change between releases. The socket protocol is documented; discovery is inference. If a release moves things, `cc-peer list --all` is the first thing to run.

## License

MIT
