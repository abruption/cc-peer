# session-peer

Local and SSH messaging for coding agent sessions. This project continues cc-peer;
its Git history and issue numbers are preserved. Codex support is tracked in #44.

## Moving from cc-peer

Install the new product explicitly: `pipx install session-peer` (or
`uv tool install session-peer`, or `python -m pip install session-peer` in a virtual
environment). No `cc-peer` command alias is installed. Both products can coexist.
After checking your workflows, remove the old package with the same manager that
installed it, e.g. `pipx uninstall cc-peer`. For a script installation, use the
`install.sh --uninstall` from the pinned cc-peer v0.5.1 tag; check its paths before
running it. New uninstall only removes session-peer files.

The standalone installer places the program in
`~/.local/share/session-peer/session_peer.py`, its CLI link in `~/.local/bin`, and
its Claude skill in `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/session-peer`. Pip
installs only the CLI. Claude/Codex configuration and old installations are not
migrated or removed automatically. `SESSION_PEER_REPLY_HOST` takes precedence over
the compatibility input `CC_PEER_REPLY_HOST`.

`cc-peer` 0.5.1 is the final Claude-only compatibility line, not an ongoing feature
or security-maintenance promise. The frozen root `cc_peer.py` is retained in tags
for old self-update URLs but is excluded from the new wheel and sdist. Its local
update command directs users here instead of installing a different product.

Package-managed installations must use their package manager to upgrade.
`session-peer update` replaces only independently installed scripts; remote
updates push the standalone program to the destination's neutral data directory.


[![PyPI](https://img.shields.io/pypi/v/session-peer)](https://pypi.org/project/session-peer/)
[![CI](https://github.com/abruption/session-peer/actions/workflows/ci.yml/badge.svg)](https://github.com/abruption/session-peer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/pypi/pyversions/session-peer)](https://pypi.org/project/session-peer/)

Message a Claude Code session on **another machine over SSH**, without the message leaving your network.

```console
$ session-peer list --host build-server
Sessions on build-server:
NAME             PID    STATUS  CWD
api-worker       4011   idle    /srv/api
migration-watch  4614   busy    /srv/api

$ session-peer send --host build-server --to api-worker "Schema migration landed; rebase is safe now."
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

`session-peer` is for the cases where it isn't available:

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

So `session-peer` doesn't forward the socket. It runs the same write **inside a remote shell**, where the connection is local again. `--host` pipes this script over SSH to `python3 -`, so nothing needs to be installed on the remote machine.

### Read the socket path; never guess it

Session records live in `~/.claude/sessions/<pid>.json` and carry the socket path. It is not always `/tmp/cc-socks/` — on two Ubuntu hosts running the same Claude Code build, one bound under `/tmp/cc-socks/` and the other under `/run/user/1001/cc-socks/`. Claude Code also falls back to a private per-user directory when it rejects the one it would have used. `session-peer` reads `messagingSocketPath` out of the record and treats a live PID with no bound socket as unreachable.

## Install

Python 3.9+, standard library only — no external dependencies.

### pip

```bash
pip install session-peer
```

Or with [pipx](https://pipx.pypa.io/) for an isolated install:

```bash
pipx install session-peer
```

pip installs the `session-peer` command but not the [Claude Code skill](#the-skill).
To add the skill so Claude can use session-peer on its own:

```bash
mkdir -p ~/.claude/skills/session-peer
curl -fsSL -o ~/.claude/skills/session-peer/SKILL.md \
  https://raw.githubusercontent.com/abruption/session-peer/main/skills/session-peer/SKILL.md
```

### install.sh

Installs both the command and the skill in one step. Use this for air-gapped
hosts or remote deployment over SSH:

```bash
git clone https://github.com/abruption/session-peer && cd session-peer

./install.sh                          # this machine
./install.sh --host build-server      # a remote machine, over SSH
./install.sh --host web-01 --host db  # several at once
```

That drops `session_peer.py` and its [Claude Code skill](skills/session-peer/SKILL.md) into
`~/.claude/skills/session-peer/`, and links `~/.local/bin/session-peer`. Nothing else is touched.
Remove it with `./install.sh --uninstall [--host ...]`.

`session-peer update` refreshes this machine from the latest GitHub release. For another machine,
`./install.sh --host <host>` pushes this copy over SSH — deliberately, since a target with no
route to GitHub is one of the cases this tool exists for. `session-peer update --host` reports what
that machine has rather than trying to make it fetch.

**Remote installs push the files over the SSH connection itself**, so the target needs
no internet access — which matters, since air-gapped hosts are one of the reasons this
exists. It needs `python3` and your SSH access, nothing more.

Or skip the installer entirely and copy the one file:

```bash
curl -O https://raw.githubusercontent.com/abruption/session-peer/main/session_peer.py
chmod +x session_peer.py
```

### The skill

Installing puts a skill next to the script, so Claude picks the target session and
writes the message itself when you ask it to reach a session on another box. The
script keeps the deterministic part — resolving a session, writing the socket — and
the skill only decides *what to send where*. Discovery leans on a session-record
schema that isn't part of Claude Code's documented interface, so it is described in
prose the agent can adapt rather than hardcoded logic that silently breaks.

## Usage

```bash
session-peer list                                  # sessions on this machine
session-peer list --host web-01                    # sessions over there
session-peer list --host web-01 --all              # include stale records / no inbox

session-peer send --to api-worker "message"        # local session
session-peer send --host web-01 --to api-worker "message"
session-peer send --host web-01 --to 4011 "message"          # address by pid
git log --oneline -5 | session-peer send --host web-01 --to api-worker -   # stdin

session-peer send --host web-01 --to api-worker --dry-run "x"   # resolve only
session-peer list --host web-01 --json             # machine-readable

# Versions. list --host reports what that machine has installed and flags a
# mismatch, since a host left behind by a release won't say so on its own.
session-peer update --check                        # is there a newer release?
session-peer update                                # replace this installation
session-peer update --host web-01                  # report what's over there
session-peer send --host web-01 --ssh-opt=-p --ssh-opt=2222 --to api-worker "..."   # note the '='

# Envelope. Sends carry who they're from and how to answer, both resolved from
# the session session-peer is running inside.
session-peer send --host web-01 --to api-worker --no-reply-to "..."         # no return address
session-peer send --host web-01 --to api-worker --no-from "..."             # no From: header
session-peer send --host web-01 --to api-worker --reply-to 100.64.0.5 "..." # state the address
```

Exit codes: `0` posted, `1` error, `2` no such session, `130` interrupted (Ctrl-C).

### Environment variables

| Variable | Effect |
| :-- | :-- |
| `SESSION_PEER_REPLY_HOST` | Override the reply address. Resolution order: `--reply-to` flag → `SESSION_PEER_REPLY_HOST` → auto-detected Tailscale IP. Useful on VPNs where Tailscale isn't installed — set it once instead of passing `--reply-to` on every call. |
| `CLAUDE_CONFIG_DIR` | Where Claude Code keeps its config (default `~/.claude`). Respected by `session-peer list` for session discovery and by `install.sh` for skill placement. |
| `ANTHROPIC_CONFIG_DIR` | Fallback if `CLAUDE_CONFIG_DIR` is unset. |

## The receiving side decides what happens next

**"Posted" is not "delivered."** Writing to the socket succeeds; whether Claude ever reads the message is up to that session's [inbound controls](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages).

The default that surprises people: **a session running with `--dangerously-skip-permissions` holds every incoming message for its user's approval**, unless the sender identifies itself as also bypassing. A script can't make that claim, so on an unattended bypass-mode worker your message sits in an approval dialog and expires after `dialogExpiry` (5 minutes by default).

For a worker meant to take messages unattended, set this in its settings:

```json
{ "crossSessionInbound": "accept" }
```

Or start it with `--settings '{"crossSessionInbound":"accept"}'`. Do this deliberately: it means anything that can write to that socket can start a turn on that machine.

Two things worth knowing before you set it:

- **It applies to sessions that are already running.** No restart needed. Measured on two sessions
  up for 144h and 4h that predated the setting entirely — both took a posted message ~3s later with
  no approval dialog, while still showing `⏵⏵ bypass permissions on`.
- **User settings are per OS user, not per session.** Putting `accept` in `~/.claude/settings.json`
  opens *every* session that user runs, not just the worker you meant. Scope it with project
  settings or `--settings` if you want one session to accept and the rest to keep asking.

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

- **The receiver is told who sent the message.** Claude Code records a socket-posted message
  with `from: "unknown"` — it has a field for the sender and nothing to put in it. Sends
  therefore open with `From: <user>@<host> (<session>)`. What Claude Code already supplies,
  session-peer does not repeat: it prefaces peer messages and appends its own guidance about what
  a peer may ask for, so duplicating either would compound with every hop.
- **Replies depend on SSH working the other way.** Sends append a `Reply:` line naming this
  machine's tailnet address and this session, so the receiver can answer — but only if that
  machine can SSH back. When it can't, neither side is told. The line grants nothing on its own:
  anyone who can reply could already have sent unprompted. What it adds is *knowing* that.
- **No discovery across a bastion.** `--host` is a single SSH hop; chain it yourself with an SSH config `ProxyJump`.
- **Same OS user.** The socket is restricted to the user that owns the session, so `session-peer` gives you nothing you couldn't already do with your own shell on that host. It is not a privilege-escalation path — but it does mean anyone with that shell can start a turn.
- **`--host` and `--ssh-opt` are as trusted as your ssh config.** They are handed to `ssh`, so whoever controls them controls where you connect. Values that would make ssh run a local command (`ProxyCommand` and friends) are refused, and a `--host` starting with `-` is rejected outright — but if you allowlist `session-peer` for an agent, treat it as granting SSH, not just messaging. Message bodies and session names carry no such risk: they are quoted before they reach any shell.
- **Windows support.** Native Windows sessions use named pipes instead of Unix sockets, and require an auth line before the message. Both are handled automatically — the auth token is read from the session's `.key` file. `install.sh` is POSIX sh and won't run on Windows; use `pip install session-peer` there instead.

## Tests

```bash
python3 -m unittest test_session_peer -v
```

No network, no SSH, no Claude Code — standard library only. CI runs them on Ubuntu and
macOS against Python 3.9 and 3.13, plus `shellcheck` on the installer.

The cases cover what has actually been wrong here: the argument quoting that stops `--to`
reaching a remote shell, the caps that weren't enforced under `--dry-run`, and the reply
line's user and absolute path. A regression in any of those is silent otherwise.

## Verified

Claude Code **v2.1.263** across five machines over Tailscale SSH — two macOS 26 (Apple silicon),
two Ubuntu 24.04 (arm64, Oracle Ampere A1 in separate regions), and one Windows 10 22H2. What was
actually exercised:

- Posting from macOS to Linux sessions in two regions; each landed in the receiving transcript as
  `type: user` with `origin.kind: "peer"`.
- Payload integrity — quotes, backticks, `$HOME`, and emoji arrive byte-for-byte.
- The held path: a bypass-mode session raised an approval dialog, then logged
  `Released 1 held cross-session message` once approved.
- Both socket layouts in the wild: `/tmp/cc-socks/` on one Ubuntu host, `/run/user/1001/cc-socks/`
  on another running the same build.
- Windows named pipe transport (`\\.\pipe\LOCAL\cc-msg-<hash>`) with mandatory auth line read from
  the session's `.key` file. `list`, `send`, and `--host` all verified on the Windows machine.

The session record schema (`~/.claude/sessions/*.json`) is not part of Claude Code's documented interface and can change between releases. The socket protocol is documented; discovery is inference. If a release moves things, `session-peer list --all` is the first thing to run.

## License

MIT
