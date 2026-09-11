# session-peer

[![PyPI](https://img.shields.io/pypi/v/session-peer)](https://pypi.org/project/session-peer/)
[![CI](https://github.com/abruption/session-peer/actions/workflows/ci.yml/badge.svg)](https://github.com/abruption/session-peer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/pypi/pyversions/session-peer)](https://pypi.org/project/session-peer/)

Message **Claude Code and Codex sessions**, locally or over SSH, from one CLI.
Claude targets use their native inbox socket/pipe; Codex targets use `codex queue`.
SSH runs the same Python script on the destination, so session-peer need not be
installed there to receive a send or list request.

This project continues cc-peer with its Git history and issue numbers preserved.
[session-peer 0.6.0](https://pypi.org/project/session-peer/0.6.0/) is released;
the old PyPI cc-peer project is archived after its final 0.5.1 release.
See [Moving from cc-peer](#moving-from-cc-peer) for explicit migration steps.

## Quick start

Install the CLI with `pipx install session-peer` or `uv tool install session-peer`.
For the standalone CLI plus Claude skill, see [Install](#install).

```bash
session-peer list                              # Claude sessions (default)
session-peer list --agent claude                # explicit equivalent
session-peer list --agent codex                 # saved Codex threads
session-peer list --agent codex --host worker   # saved threads on an SSH host

session-peer send --to api-worker "message"     # Claude name or PID
session-peer send --to 'codex:<full-thread-uuid>' "message"
session-peer send --host worker --to 'codex:<full-thread-uuid>' --dry-run "message"
```

Replace `<full-thread-uuid>` with a full ID from the destination's Codex listing.
`list` selects one agent at a time; it does not merge both agents. **Use
`--agent codex`, not `--codex`**: `--codex` is not a supported flag and is ambiguous
with `--codex-home` and `--codex-bin`. `send` selects the agent from its target,
not a `--agent` flag.

**Posted/queued is not acknowledged.** Saved Codex threads are not necessarily
running, and session-peer does not wake/resume a session or wait for its reply.

## Install

Python 3.9+, standard library only — no external dependencies.

### pip

In an activated virtual environment:

```bash
python -m pip install session-peer
```

Or with [pipx](https://pipx.pypa.io/) for an isolated install:

```bash
pipx install session-peer
```

Alternatively, use `uv tool install session-peer`.
Package managers install the `session-peer` command but not the [Claude Code skill](#the-skill).
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

That places `session_peer.py` in `~/.local/share/session-peer/`, installs the
[Claude Code skill](skills/session-peer/SKILL.md) in `~/.claude/skills/session-peer/`,
and links `~/.local/bin/session-peer`. Existing cc-peer files are preserved.
Remove it with `./install.sh --uninstall [--host ...]`.

`session-peer update` refreshes a standalone program from the latest GitHub release.
`./install.sh --host <host>` pushes this checkout's program and skill over SSH.
`session-peer update --host <host>` pushes only the program when the installed
version differs or is absent; add `--check` to report without changing anything.
See [Updating](#updating) for package-managed installs and remote limitations.

**Remote installs push the files over the SSH connection itself**, so the target needs
no internet access. Installing the standalone files requires `python3` and SSH
access. Messaging also requires the selected agent's native inbox or queue on
the destination.

Or skip the installer entirely and copy the one file:

```bash
curl -O https://raw.githubusercontent.com/abruption/session-peer/main/session_peer.py
chmod +x session_peer.py
```

### The skill

The standalone installer puts the program in `~/.local/share/session-peer/`
and the skill separately in `~/.claude/skills/session-peer/SKILL.md`. Skill placement
respects `CLAUDE_CONFIG_DIR`, then `ANTHROPIC_CONFIG_DIR`, before the default.
The skill guides Claude's choice of target and message; the Python program
performs discovery and transport. Installing it does not install a Codex
plugin or change either agent's permissions or inbound settings.

## Usage

```bash
session-peer list                                  # Claude sessions on this machine
session-peer list --host web-01                    # Claude sessions over there
session-peer list --host web-01 --all              # Claude stale records / no inbox
session-peer list --agent codex --all              # include archived Codex threads

session-peer send --to api-worker "message"        # local session
session-peer send --host web-01 --to api-worker "message"
session-peer send --host web-01 --to 4011 "message"          # address by pid
git log --oneline -5 | session-peer send --host web-01 --to api-worker -   # stdin

session-peer send --host web-01 --to api-worker --dry-run "x"   # resolve only
session-peer list --host web-01 --json             # machine-readable

session-peer send --host web-01 --ssh-opt=-p --ssh-opt=2222 --to api-worker "..."   # note the '='

# Envelope. Sends carry who they're from and how to answer, both resolved from
# the Claude session session-peer is running inside, when available.
session-peer send --host web-01 --to api-worker --no-reply-to "..."         # no return address
session-peer send --host web-01 --to api-worker --no-from "..."             # no From: header
session-peer send --host web-01 --to api-worker --reply-to 100.64.0.5 "..." # state the address
```

Repeat `--host` to operate on several SSH destinations. `--json` is available on
`list`, `send`, and `update`; v0.6 retains command-specific response shapes rather
than a uniform envelope. See [#29](https://github.com/abruption/session-peer/issues/29).

Exit codes: `0` successful command (including listing or dry-run), `1` operational
error, `2` CLI usage error or an unresolved target reported as no-target, and
`130` interrupted (Ctrl-C). A Codex queue rejection, including a missing rollout,
is an operational error (`1`); a missing saved thread during dry-run returns `2`.
Exit `0` on send does not confirm consumption or a reply.

### Updating

For package-managed installs, use the same manager that installed the command:

```bash
pipx upgrade session-peer
# or: uv tool upgrade session-peer
# or, in its virtual environment: python -m pip install --upgrade session-peer
```

For these installs, local `session-peer update` (including `--check`) prints
package-manager guidance without checking GitHub or replacing the package's files.

For standalone programs:

```bash
session-peer update --check                       # check the latest GitHub release
session-peer update                               # update the local program
session-peer update --host web-01 --check         # inspect the remote standalone copy
session-peer update --host web-01                 # push this program if versions differ
```

Remote update compares against the **local program's version**, not the latest
GitHub release. It pushes even if the remote version is newer; check first and
update the local program before distributing it. The remote copy does not fetch
from GitHub. Remote version probes inspect only
`~/.local/share/session-peer/session_peer.py`, not pip/pipx/uv installations;
remote update installs that standalone path and its CLI link. For a
package-managed remote CLI, upgrade it with its own manager on that host instead.

Neither local nor remote `update` refreshes the Claude skill. Re-run `install.sh`
from the desired release checkout to refresh both standalone program and skill.

### Environment variables

| Variable | Effect |
| :-- | :-- |
| `SESSION_PEER_REPLY_HOST` | Override the advertised reply host: `--reply-to` → `SESSION_PEER_REPLY_HOST` → `CC_PEER_REPLY_HOST` → auto-detected Tailscale IP. A detectable Claude sender session is still required for a reply line. |
| `CC_PEER_REPLY_HOST` | Legacy fallback; prefer `SESSION_PEER_REPLY_HOST` for new configuration. |
| `CLAUDE_CONFIG_DIR` | Where Claude Code keeps its config (default `~/.claude`). Respected by `session-peer list` for session discovery and by `install.sh` for skill placement. |
| `ANTHROPIC_CONFIG_DIR` | Fallback if `CLAUDE_CONFIG_DIR` is unset. |
| `CODEX_HOME` | Codex discovery/queue home (default `~/.codex`); overridden by `--codex-home`. |

With `--host`, discovery uses the destination's environment; local environment
variables are not automatically forwarded. `--codex-home` and `--codex-bin`
explicitly select paths on that destination.

## Codex sessions

Codex discovery reads `state_5.sqlite` using a read-only SQLite connection.
This internal schema is experimental, tested with Codex CLI 0.154.0 on macOS;
cross-platform fixture tests are not a claim of live Codex verification on all OSes.
Saved sessions are not necessarily active. `--all` includes archived threads.

`--codex-home` overrides the destination's `CODEX_HOME` (default `~/.codex`).
`--codex-bin` overrides its PATH lookup of `codex` for sending. On SSH these are
remote paths. Sending requires a Codex executable with the `queue` command and
the appropriate saved thread/rollout in that home; a listing alone does not
prove the queue can accept it.

Submission uses `codex queue`, never direct database writes. `queued` means the
CLI accepted the submission, not that a turn consumed it or acknowledged it.
session-peer does not wake or resume sessions. Queue DB writes and Claude socket
connections may require approval in the caller's execution environment; the tool
does not change sandbox or inbound policies. A queue timeout (30 seconds) has an
unknown submission outcome: inspect the destination before retrying. It is not
a timeout for waiting on a reply, and session-peer does not automatically retry.

Codex messages are limited to 32 KiB of UTF-8 including sender/reply headers, as a
session-peer portability policy rather than a measured Codex server limit. NUL
characters cannot be passed as CLI arguments. `--dry-run` verifies the executable
and saved target without queueing but cannot guarantee a later submission will succeed.

Local Codex list JSON has the `{sessions, version}` envelope. Each entry has
`agent`, `id`, `name` (first line, at most 120 characters), `cwd`, `updatedAt`
(Unix seconds), and `archived`. Remote list JSON is an array of per-host results,
even for one host; each successful result includes `host`, `sessions`, `version`
and an optional `remoteVersion` for an installed standalone copy.

Codex send JSON adds `target: {agent, id}` and `status: queued` (or `validated`
under dry-run) to `{ok, chars, dryRun}`; `queueId` is optional. A remote send to
one host returns a flat object with `host`; multiple hosts return an array.
Claude output stays compatible. Existing identity/reply detection identifies
Claude senders, not Codex senders: a Codex target is supported, but an automatic
return address for a Codex sender is not yet provided.

`doctor`, reply waiting/`--wait`, structured reply URIs and optional wake are
future work, not v0.6 features: see the [v0.7](https://github.com/abruption/session-peer/issues/45)
and [v0.8](https://github.com/abruption/session-peer/issues/46) roadmaps.

## Moving from cc-peer

The repository rename and package transition are complete:
[session-peer 0.6.0](https://pypi.org/project/session-peer/0.6.0/) is published and
[cc-peer 0.5.1](https://pypi.org/project/cc-peer/0.5.1/) is the final Claude-only
compatibility release. **The old PyPI cc-peer project is archived; the GitHub
session-peer repository remains active.** Existing legacy distributions remain
downloadable and are not yanked for migration. See the completed [transition
issue #48](https://github.com/abruption/session-peer/issues/48).

Install the new product explicitly with `pipx install session-peer`,
`uv tool install session-peer`, or the [standalone installer](#installsh).
No `cc-peer` command alias is installed. Both products can coexist.
After checking your workflows, remove the old package with the same manager that
installed it, e.g. `pipx uninstall cc-peer`. For a script installation, use the
`install.sh --uninstall` from the pinned cc-peer v0.5.1 tag; check its paths and
back up local customizations before running it. New uninstall only removes
session-peer files.

Update scripts and agent instructions to call `session-peer` and use the new
standalone program path `~/.local/share/session-peer/session_peer.py`, not the old
`~/.claude/skills/cc-peer/cc_peer.py`. The new Claude skill lives separately in
`~/.claude/skills/session-peer/`. Claude/Codex configuration, session data and old
installations are not migrated or removed automatically. Replace
`CC_PEER_REPLY_HOST` with `SESSION_PEER_REPLY_HOST` when convenient; the old
variable remains a fallback.

The final cc-peer release is not an ongoing feature or security-maintenance
promise. The frozen root `cc_peer.py` is retained in tags for old self-update
URLs but is excluded from the new wheel and sdist. Its local update command
directs users here instead of installing a different product.

## Claude Code sessions

### Use the official feature first

For Claude-to-Claude workflows, consider Claude Code's built-in
[cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging)
and [Remote Control](https://code.claude.com/docs/en/remote-control) first.
session-peer provides a shell-driven local/SSH path when that workflow is not
available or suitable, and a common CLI for Claude and Codex targets. The
Claude-specific guidance in this section is not a prerequisite for Codex queues.

### How the inbox transport works

Claude Code's [session inbox socket](https://code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket)
accepts a JSON line:

```json
{"type":"user","message":{"role":"user","content":"your message"}}
```

session-peer connects on the machine that owns that inbox. For SSH sends, it
pipes its source to remote `python3 -` and makes the connection there, rather
than forwarding the Unix socket. Native Windows targets use a named pipe and
the auth line from the session's `.key` file instead.

Session records normally live in `~/.claude/sessions/<pid>.json` and carry the
socket path in `messagingSocketPath`. Never guess `/tmp/cc-socks/`: previous
tests found both that layout and `/run/user/1001/cc-socks/`. A live PID without
a bound inbox is treated as unreachable. The record schema is an internal
interface and may change; use `session-peer list --all` to inspect stale or
inbox-less records. A record or socket's existence does not guarantee that the
caller has permission to write to it.

### The receiving side decides what happens next

**"Posted" is not "delivered."** Writing to the socket succeeds; whether Claude ever reads the message is up to that session's [inbound controls](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages).

Messages can be held for approval or refused. In the legacy verification, a
bypass-mode receiver held script-originated messages until approved. Do not
infer receipt from a successful socket write or change permission modes to
make a test pass.

If you deliberately want a worker to accept incoming messages unattended,
configure that receiver explicitly:

```json
{ "crossSessionInbound": "accept" }
```

Scope this with project settings or `--settings` if it is intended for one worker;
user settings affect other sessions for that OS user too. Accepting messages can
start receiving turns and consume usage. session-peer never applies this setting
for you, and the receiver's own tool permissions still apply.

### Why not `tmux send-keys`?

Terminal keystrokes can land in a running subprocess or permission prompt, not
the intended chat input. session-peer uses the agent's inbox/queue boundary
instead. Claude treats an inbox message as peer text, subject to its
[receiving-session rules](https://code.claude.com/docs/en/cross-session-messaging#how-a-session-treats-an-incoming-message),
not as the user typing approval.

## Limits

- **Sender identity is best-effort.** When running inside a detectable Claude
  session, session-peer can add a textual `From:` header. Outside that context,
  it may omit it. This is not an authenticated identity protocol, and Codex
  sender detection is not implemented in v0.6.
- **Advertised replies need a working return path.** When a Claude sender and
  reply host can be determined, a `Reply:` line supplies an SSH return command.
  Forward SSH success does not prove reverse SSH access. v0.6 does not verify
  that route, correlate a reply, or wait for one; the address grants no access.
- **No discovery across a bastion.** `--host` is a single SSH hop; chain it yourself with an SSH config `ProxyJump`.
- **Destination user and execution permissions matter.** Claude inboxes and
  Codex state/queues belong to the destination account. Use the correct account
  and home; a caller's sandbox may still deny access. session-peer does not
  bypass either agent's permissions or quota.
- **`--host` and `--ssh-opt` are as trusted as your ssh config.** They are handed to `ssh`, so whoever controls them controls where you connect. Values that would make ssh run a local command (`ProxyCommand` and friends) are refused, and a `--host` starting with `-` is rejected outright — but if you allowlist `session-peer` for an agent, treat it as granting SSH, not just messaging. Message bodies and session names carry no such risk: they are quoted before they reach any shell.
- **Windows support.** Claude's named pipe transport is supported.
  `install.sh` and the standalone remote installer/updater use POSIX shell;
  use a Python package manager on native Windows. Live Codex verification
  for v0.6 was on macOS, not Windows.

## Tests

```bash
python3 -m unittest discover -v
```

The suite needs no live agent, SSH server or network. It includes legacy
compatibility tests, shared CLI helpers, fixture-based Codex discovery and queue
subprocess tests, and isolated standalone installation/coexistence checks.
Codex coverage includes argv/payload handling, destination home selection,
dry-run without dispatch, failure/timeout semantics and remote option forwarding.

CI runs tests on Ubuntu/macOS with Python 3.9 and 3.13, and Windows with Python
3.13 (POSIX installer tests are skipped there). Separate jobs check shell syntax
with `shellcheck`, standalone install/reinstall/uninstall, and wheel/sdist
contents and installation. This is not full transport coverage: Claude discovery
fixtures, real UDS payload checks and broader exit-code/remote-command regression
tests remain tracked in [#28](https://github.com/abruption/session-peer/issues/28).

## Verified

### session-peer v0.6.0 transition (2026-09-10)

- 134 local tests and the release CI checks passed. Wheel/sdist builds and
  isolated installs, actual PyPI installs, package-manager update protection,
  and coexistence/removal of the legacy CLI were checked.
- With Codex CLI **0.154.0 on two macOS machines**, local/SSH saved-session
  discovery, dry-run and actual queue submission worked. The submitted bodies
  matched the queue records. Those checks established **queued**, not consumed
  or acknowledged, and did not establish a reliable active-session indicator.
- Claude local/SSH inbox writes through the new CLI succeeded. A weekly usage
  limit prevented fresh receiving-turn/reply verification; those writes are
  not claimed as completed round trips.
- Standalone CLI/Claude skill migration was checked on two macOS hosts and two
  Ubuntu hosts. Installation and read-only discovery checks did not start agent
  turns or change inbound settings.

See [RELEASING.md](RELEASING.md) and [#48](https://github.com/abruption/session-peer/issues/48)
for the release sequence and verification limits.

### Historical cc-peer Claude transport verification

Before the rename, Claude Code **v2.1.263** was exercised across five machines
over SSH on a Tailscale network: two macOS 26 (Apple silicon), two Ubuntu 24.04
(arm64, Oracle Ampere A1 in separate regions), and one Windows 10 22H2.
These historical observations are not a claim that every test was repeated
with session-peer v0.6.0:

- Posting from macOS to Linux sessions in two regions; each landed in the receiving transcript as
  `type: user` with `origin.kind: "peer"`.
- Payload integrity — quotes, backticks, `$HOME`, and emoji arrive byte-for-byte.
- The held path: a bypass-mode session raised an approval dialog, then logged
  `Released 1 held cross-session message` once approved.
- Both socket layouts in the wild: `/tmp/cc-socks/` on one Ubuntu host, `/run/user/1001/cc-socks/`
  on another running the same build.
- Windows named pipe transport (`\\.\pipe\LOCAL\cc-msg-<hash>`) with mandatory auth line read from
  the session's `.key` file. `list`, `send`, and `--host` all verified on the Windows machine.

Both agents' discovery formats can change between upstream releases. A previous
successful transport test does not guarantee discovery or delivery in a newer
agent build.

## License

MIT
