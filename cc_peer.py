#!/usr/bin/env python3
"""cc-peer — message Claude Code sessions on another machine, over SSH.

Claude Code delivers a cross-session message one of three ways:

    same machine      -> per-session Unix domain socket, never leaves the host
    another machine   -> through Anthropic's servers, over Remote Control
    Claude on the web -> through Anthropic's servers

This script covers the gap: another machine you can already reach over SSH,
with nothing leaving your network. It runs the same socket write the docs
describe under "The session's inbox socket", except it runs it inside a remote
shell instead of a local one.

Use the official Remote Control when you can. Reach for this when you can't:
Bedrock / Vertex / Foundry, API-key auth, air-gapped networks, or unattended
workers that nobody is around to connect.

Single file, standard library only. Copy it wherever you need it; `--host`
ships a copy of itself over SSH, so the remote machine needs nothing installed.

Verified against Claude Code v2.1.263 on macOS and Ubuntu.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

__version__ = "0.3.0"
GITHUB_REPO = "abruption/cc-peer"

# Claude Code refuses a same-machine message once its serialized form passes
# about a million characters, so fail here rather than at the far end.
MAX_MESSAGE_CHARS = 1_000_000

# Over SSH the message travels as a command-line argument, so it meets Linux's
# MAX_ARG_STRLEN (128 KB per argument) long before the cap above. base64 costs
# 4/3, and the rest of the command needs room, so keep well under it.
MAX_REMOTE_MESSAGE_CHARS = 90_000
CONNECT_TIMEOUT = 10.0
DRAIN_TIMEOUT = 2.0
DETECT_TIMEOUT = 3.0

# Tailscale hands out addresses from the CGNAT range, 100.64.0.0/10. Matching on
# "100." alone would also catch ordinary public addresses like 100.200.x.x.
TAILNET_SECOND_OCTET = range(64, 128)

EXIT_ERROR = 1
EXIT_NO_TARGET = 2


class CcPeerError(Exception):
    """Anything the user should see as a one-line failure."""


# --------------------------------------------------------------------------
# Discovery. Runs on whichever machine owns the sessions — locally when there
# is no --host, inside the remote shell when there is.
# --------------------------------------------------------------------------


def sessions_dir() -> Path:
    """Where Claude Code keeps its per-session records."""
    for var in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_CONFIG_DIR"):
        root = os.environ.get(var)
        if root:
            return Path(root) / "sessions"
    return Path.home() / ".claude" / "sessions"


def pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def discover(include_unreachable: bool = False) -> list[dict]:
    """List this machine's Claude Code sessions.

    The socket path is read from each record, never guessed. It is not always
    under /tmp: a session may bind under $XDG_RUNTIME_DIR, or under a private
    per-user directory when Claude Code rejects the one it would have used.
    """
    found: list[dict] = []
    directory = sessions_dir()
    if not directory.is_dir():
        return found

    for record_file in sorted(directory.glob("*.json")):
        if not record_file.stem.isdigit():
            continue
        try:
            record = json.loads(record_file.read_text())
        except (OSError, ValueError):
            continue

        pid = record.get("pid")
        if not isinstance(pid, int):
            continue

        sock = record.get("messagingSocketPath") or ""
        alive = pid_alive(pid)
        # A live session without a bound socket has no inbox and cannot be
        # messaged; a dead one leaves its record behind until it is swept.
        has_inbox = bool(sock) and Path(sock).is_socket()

        entry = {
            "pid": pid,
            "name": record.get("name"),
            "status": record.get("status"),
            "cwd": record.get("cwd"),
            "kind": record.get("kind"),
            "version": record.get("version"),
            "tmux": record.get("tmux"),
            "socket": sock,
            "alive": alive,
            "reachable": alive and has_inbox,
        }
        if entry["reachable"] or include_unreachable:
            found.append(entry)

    return found


def resolve_target(sessions: list[dict], target: str) -> dict:
    """Find one session by name, or by pid when the target is all digits."""
    reachable = [s for s in sessions if s["reachable"]]

    if target.isdigit():
        matches = [s for s in reachable if s["pid"] == int(target)]
    else:
        wanted = target.casefold()
        matches = [s for s in reachable if (s["name"] or "").casefold() == wanted]

    if not matches:
        known = ", ".join(sorted(s["name"] or str(s["pid"]) for s in reachable))
        raise CcPeerError(
            f"no reachable session named {target!r}"
            + (f" (reachable: {known})" if known else " (no reachable sessions)")
        )
    if len(matches) > 1:
        pids = ", ".join(str(s["pid"]) for s in matches)
        raise CcPeerError(
            f"{len(matches)} sessions answer to {target!r} (pids: {pids}) — "
            f"address one by pid instead"
        )
    return matches[0]


def check_message(text: str, remote: bool) -> None:
    """Reject a message that can't be delivered, before anything is sent.

    Kept out of post_to_socket so --dry-run and the remote path get the same
    answer as a real send: a rehearsal that passes and a send that fails is
    worse than no rehearsal.
    """
    if not text.strip():
        raise CcPeerError("refusing to send an empty message")
    if len(text) > MAX_MESSAGE_CHARS:
        raise CcPeerError(
            f"message is {len(text)} characters; the limit is {MAX_MESSAGE_CHARS}"
        )
    if remote and len(text) > MAX_REMOTE_MESSAGE_CHARS:
        raise CcPeerError(
            f"message is {len(text)} characters; over SSH the limit is "
            f"{MAX_REMOTE_MESSAGE_CHARS}, because it travels as a command-line "
            f"argument. Send it from a session on that machine to use the full "
            f"{MAX_MESSAGE_CHARS}."
        )


def post_to_socket(socket_path: str, text: str) -> None:
    """Write one message to a session's inbox socket.

    On macOS and Linux the {"type":"auth",...} line the docs describe is
    optional, so this sends the message on its own. Claude Code closes a
    connection that has not sent a complete line within 30 seconds, so the
    message is built before the socket is opened.
    """
    check_message(text, remote=False)

    payload = json.dumps(
        {"type": "user", "message": {"role": "user", "content": text}},
        ensure_ascii=False,
    )

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(CONNECT_TIMEOUT)
    try:
        try:
            conn.connect(socket_path)
        except OSError as exc:
            raise CcPeerError(f"cannot reach inbox at {socket_path}: {exc}") from exc

        try:
            conn.sendall((payload + "\n").encode("utf-8"))
            # Half-close, then give the far end a moment before we drop the
            # connection. Claude Code sends nothing back, so this confirms
            # delivery no more than the write itself does — it only avoids
            # closing so abruptly that a just-written line goes unread.
            conn.shutdown(socket.SHUT_WR)
            conn.settimeout(DRAIN_TIMEOUT)
            try:
                conn.recv(1)
            except OSError:
                pass
        except OSError as exc:
            raise CcPeerError(f"failed writing to {socket_path}: {exc}") from exc
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Reply address. A cross-machine message carries no reply address of its own,
# so the receiving Claude has no way to know answering is even possible. This
# appends one line saying where to send an answer.
#
# It grants nothing: the far side can only reply if it could already SSH here.
# What it adds is knowing that, which is what the receiver otherwise lacks.
# --------------------------------------------------------------------------


def is_tailnet_address(candidate: str) -> bool:
    parts = candidate.split(".")
    if len(parts) != 4 or not all(p.isdigit() and len(p) <= 3 for p in parts):
        return False
    octets = [int(p) for p in parts]
    if any(o > 255 for o in octets):
        return False
    return octets[0] == 100 and octets[1] in TAILNET_SECOND_OCTET


def _run(command: list[str]) -> str:
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=DETECT_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout


def detect_reply_host() -> str | None:
    """This machine's tailnet address, or None if it can't be determined.

    `tailscale ip -4` is the direct answer where the CLI is on PATH. On macOS it
    usually isn't — the app ships it inside the bundle — so fall back to reading
    it off the interfaces.
    """
    for command in (["tailscale", "ip", "-4"], ["ip", "-4", "-o", "addr", "show"], ["ifconfig"]):
        for candidate in re.findall(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", _run(command)):
            if is_tailnet_address(candidate):
                return candidate
    return None


def own_session() -> dict | None:
    """The session this process is running inside, if any.

    Claude Code exports the session's own inbox socket path, which carries its
    pid; the registry turns that into a name. Empty when run from a plain shell.
    """
    socket_path = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
    match = re.search(r"(\d+)\.sock$", socket_path)
    if not match:
        return None
    pid = int(match.group(1))
    for session in discover(include_unreachable=True):
        if session["pid"] == pid:
            return session
    return None


def sender_identity(explicit_host: str | None) -> tuple[str, str | None] | None:
    """(session name, reachable address) for the session we're running in.

    Both the From: header and the Reply: line are built from this, so they
    can't drift apart. None outside a session, where there is no name to give.
    """
    session = own_session()
    if session is None:
        return None
    name = session["name"] or str(session["pid"])
    host = explicit_host or os.environ.get("CC_PEER_REPLY_HOST") or detect_reply_host()
    if host and "@" not in host:
        host = f"{getpass.getuser()}@{host}"
    return name, host


def from_header(explicit_host: str | None) -> str | None:
    """Who is speaking.

    Claude Code records an arriving peer message with `from: "unknown"` when
    it was posted to the socket directly, so without this the receiver has no
    idea who is asking — and it is told to treat the message as a teammate's
    request. Separate from the reply address on purpose: knowing the sender
    stays useful when answering isn't possible.
    """
    identity = sender_identity(explicit_host)
    if identity is None:
        return None
    name, host = identity
    # Fall back to the local hostname so this line survives even when no
    # tailnet address turns up; it is for reading, not for connecting.
    where = host or f"{getpass.getuser()}@{socket.gethostname()}"
    return f"From: {where} ({name})"


def wrap_message(
    text: str, explicit_host: str | None, with_from: bool, with_reply: bool
) -> str:
    """Put the body in an envelope: who sent it, and how to answer.

    Deliberately minimal. Claude Code already prefaces an arriving peer
    message and appends its own guidance about what a peer may and may not
    ask for — repeating any of that here would duplicate it in every message
    and compound with each hop. The two facts it *doesn't* have are the
    sender's identity and a working return address.
    """
    header = from_header(explicit_host) if with_from else None
    footer = reply_line(explicit_host) if with_reply else None
    body = text.strip("\n")
    parts = ([header, ""] if header else []) + [body]
    if footer:
        parts += ["", "---", footer]
    return "\n".join(parts)


def reply_line(explicit_host: str | None) -> str | None:
    """How to answer, as a command that runs verbatim.

    Three things the first version left out, each of which broke it in
    practice:

    * the **user**, because the receiver otherwise connects as its own local
      account — a worker running as `ubuntu` cannot reach a laptop's `abruptly`
    * an **absolute script path**, because a non-interactive SSH session never
      sources the profile that puts ~/.local/bin on PATH, so a bare `cc-peer`
      is not found
    * `--no-reply-to`, so answering an answer doesn't ping-pong

    The username is the sender's; there's no guarantee the far side knows it,
    but it is right whenever accounts match and strictly better than nothing.
    """
    identity = sender_identity(explicit_host)
    if identity is None:
        return None
    name, host = identity
    if not host:
        return None
    return (
        "Reply: python3 ~/.claude/skills/cc-peer/cc_peer.py send "
        f"--host {host} --to {shlex.quote(name)} --no-reply-to"
    )


# --------------------------------------------------------------------------
# Remote dispatch. Ships this file over SSH and runs it there, so the remote
# machine needs nothing installed beyond python3.
# --------------------------------------------------------------------------


# ssh options that make ssh run a command on *this* machine. A host or an
# --ssh-opt value carrying one of these turns "message a session" into "run
# whatever I say, locally". ProxyJump is deliberately absent: it takes a host,
# not a command, and is the right way to reach a box behind a bastion.
LOCAL_EXEC_SSH_OPTIONS = ("proxycommand", "localcommand", "permitlocalcommand")


def check_ssh_argument(value: str, flag: str) -> None:
    """Refuse a value that would make ssh do something other than connect.

    ssh has no `--` separator, so a leading dash turns a destination into a
    flag. Hosts never legitimately start with one, while --ssh-opt values
    always do — so the leading-dash rule applies only to the host, and both
    are checked for options that execute a local command.
    """
    if flag == "--host" and value.startswith("-"):
        raise CcPeerError(
            f"--host must not start with '-' (ssh would read {value!r} as an option)"
        )
    collapsed = value.lower().replace(" ", "").replace("=", "")
    for banned in LOCAL_EXEC_SSH_OPTIONS:
        if banned in collapsed:
            raise CcPeerError(
                f"{flag} must not carry {banned} — it would run a command on this "
                f"machine. Put it in ~/.ssh/config if you really need it."
            )


def run_remote(host: str, argv: list[str], ssh_opts: list[str]) -> dict:
    check_ssh_argument(host, "--host")
    for opt in ssh_opts:
        check_ssh_argument(opt, "--ssh-opt")

    try:
        source = Path(__file__).resolve().read_text()
    except OSError as exc:  # pragma: no cover - only when run from a pipe
        raise CcPeerError(f"cannot read own source to send to {host}: {exc}") from exc

    # ssh joins everything after the destination with spaces and hands the
    # result to the remote *shell*, so an argv list is not the protection it
    # looks like: a metacharacter in any element executes over there. Build
    # the remote command as one already-quoted string instead.
    remote = " ".join(shlex.quote(a) for a in ["python3", "-", *argv, "--json"])
    command = ["ssh", *ssh_opts, host, remote]
    try:
        completed = subprocess.run(
            command, input=source, text=True, capture_output=True, timeout=120
        )
    except FileNotFoundError as exc:
        raise CcPeerError("ssh not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CcPeerError(f"ssh to {host} timed out") from exc
    except OSError as exc:
        raise CcPeerError(f"could not run ssh to {host}: {exc}") from exc

    stdout = completed.stdout.strip()
    if not stdout:
        detail = completed.stderr.strip() or f"ssh exited {completed.returncode}"
        raise CcPeerError(f"{host}: {detail}")
    try:
        result = json.loads(stdout)
    except ValueError as exc:
        raise CcPeerError(f"{host}: unexpected output: {stdout[:200]}") from exc

    # The far end reports its own failures in-band; surface them here rather
    # than letting a caller read an error payload as a success.
    if isinstance(result, dict) and result.get("ok") is False:
        raise CcPeerError(f"{host}: {result.get('error', 'remote command failed')}")
    return result


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def render_sessions(sessions: list[dict], where: str) -> str:
    if not sessions:
        return f"No reachable Claude Code sessions on {where}."

    rows = [("NAME", "PID", "STATUS", "CWD")]
    for s in sessions:
        name = s["name"] or "(unnamed)"
        if not s["reachable"]:
            name += " [no inbox]" if s["alive"] else " [stale record]"
        rows.append((name, str(s["pid"]), s["status"] or "-", s["cwd"] or "-"))

    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = [
        f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]}"
        for r in rows
    ]
    return f"Sessions on {where}:\n" + "\n".join(lines)


def emit(as_json: bool, payload: dict, human: str) -> None:
    print(json.dumps(payload, ensure_ascii=False) if as_json else human)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    remote_version = None
    if args.host:
        result = run_remote(args.host, ["list"] + (["--all"] if args.all else []), args.ssh_opt)
        sessions = result.get("sessions", [])
        # Ask the machine what it has installed. result["version"] would only
        # echo our own, since run_remote ships and runs this very file.
        remote_version = remote_installed_version(args.host, args.ssh_opt)
    else:
        sessions = discover(include_unreachable=args.all)

    where = args.host or "this machine"
    human = render_sessions(sessions, where)
    if remote_version and remote_version != __version__:
        human = (
            f"{where} runs cc-peer {remote_version}; this machine has {__version__}."
            f"\nUpdate it with:  cc-peer update --host {args.host}\n\n{human}"
        )
    emit(
        args.json,
        {"sessions": sessions, "version": __version__,
         **({"remoteVersion": remote_version} if remote_version else {})},
        human,
    )
    return 0


def read_message(args: argparse.Namespace) -> str:
    if args.b64 is not None:
        try:
            return base64.b64decode(args.b64, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise CcPeerError(f"--b64 is not valid base64-encoded UTF-8: {exc}") from exc
    if args.message is None or args.message == "-":
        return sys.stdin.read()
    return args.message


def remote_installed_version(host: str, ssh_opts: list[str]) -> str | None:
    """Version of the copy *installed* on that machine.

    Not the same thing as asking the remote command to report itself:
    run_remote() ships our own source and runs that, so it would always echo
    our version back. The installed file is what a session over there will
    actually use, and it is what can fall behind.
    """
    check_ssh_argument(host, "--host")
    for opt in ssh_opts:
        check_ssh_argument(opt, "--ssh-opt")
    probe = 'python3 "$HOME/.claude/skills/cc-peer/cc_peer.py" --version 2>/dev/null'
    try:
        done = subprocess.run(
            ["ssh", *ssh_opts, host, probe],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip()
    return out.split()[-1] if out.startswith("cc-peer") else None


def parse_version(text: str) -> tuple[int, ...]:
    """(1, 2, 3) from "v1.2.3". Unparseable parts sort lowest."""
    parts = text.strip().lstrip("vV").split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts[:3])


def latest_release() -> tuple[str, str]:
    """(tag, download URL) of the newest release on GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=DETECT_TIMEOUT * 4) as response:
            tag = json.load(response).get("tag_name", "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CcPeerError(
            f"could not reach GitHub to check for updates: {exc}. "
            f"On a host with no route out, update it from a machine that has one: "
            f"cc-peer update --host <this host>"
        ) from exc
    if not tag:
        raise CcPeerError("GitHub returned no release tag")
    return tag, f"https://raw.githubusercontent.com/{GITHUB_REPO}/{tag}/cc_peer.py"


def cmd_update(args: argparse.Namespace) -> int:
    # Pushing from here beats asking the far side to fetch: the machines this
    # tool exists for are the ones without a route to GitHub.
    if args.host:
        there = remote_installed_version(args.host, args.ssh_opt)
        if there is None:
            human = (f"{args.host} has no cc-peer installed (or it is too old to say).\n"
                     f"Install it with:  ./install.sh --host {args.host}")
        elif there == __version__:
            human = f"{args.host} runs cc-peer {there} — same as this machine."
        else:
            human = (f"{args.host} runs cc-peer {there}; this machine has {__version__}.\n"
                     f"Push it with:  ./install.sh --host {args.host}")
        emit(args.json,
             {"host": args.host, "remoteVersion": there, "current": __version__},
             human)
        return 0

    tag, url = latest_release()
    latest, current = parse_version(tag), parse_version(__version__)

    if args.check:
        state = "up to date" if current >= latest else f"{tag} available"
        emit(
            args.json,
            {"current": __version__, "latest": tag, "outdated": current < latest},
            f"cc-peer {__version__} — {state}",
        )
        return 0

    if current >= latest:
        emit(args.json, {"current": __version__, "latest": tag, "updated": False},
             f"cc-peer {__version__} is already current ({tag}).")
        return 0

    target = Path(__file__).resolve()
    try:
        with urllib.request.urlopen(url, timeout=DETECT_TIMEOUT * 4) as response:
            source = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise CcPeerError(f"could not download {tag}: {exc}") from exc
    if b"__version__" not in source:
        raise CcPeerError(f"what came back from {url} does not look like cc_peer.py")

    # We are running from the file being replaced. Write beside it and rename,
    # so a failed download can't leave a half-written script behind.
    staged = target.with_suffix(".py.new")
    try:
        staged.write_bytes(source)
        staged.chmod(target.stat().st_mode & 0o777)
        staged.replace(target)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise CcPeerError(f"could not replace {target}: {exc}") from exc

    emit(
        args.json,
        {"current": __version__, "latest": tag, "updated": True, "path": str(target)},
        f"cc-peer {__version__} → {tag}  ({target})",
    )
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    text = read_message(args)

    # Check the body the user actually wrote. Doing this after the reply line
    # is appended would let an empty message through on the strength of the
    # line alone — which still starts a turn on the other machine.
    check_message(text, remote=bool(args.host))

    # Built here, before dispatch: detection has to run on the sender's machine.
    # Doing it on the far side would advertise the receiver's own address back
    # at it. The --b64 path is this script re-running remotely, where the
    # envelope is already part of the payload.
    if args.b64 is None:
        text = wrap_message(
            text,
            explicit_host=args.reply_to,
            with_from=not args.no_from,
            with_reply=not args.no_reply_to,
        )
        if (
            not args.no_reply_to
            and reply_line(args.reply_to) is None
            and (args.reply_to or os.environ.get("CC_PEER_REPLY_HOST"))
            and not args.json
        ):
            # A reply address names a session, and outside one there is no name
            # to give. Say so rather than dropping the flag without a word.
            print(
                "cc-peer: no reply address sent — a reply needs a session to name, "
                "and this isn't running inside one",
                file=sys.stderr,
            )

    if args.host:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        remote_argv = ["send", "--to", args.to, "--b64", encoded]
        if args.dry_run:
            remote_argv.append("--dry-run")
        result = run_remote(args.host, remote_argv, args.ssh_opt)
        target = result.get("target", {})
    else:
        session = resolve_target(discover(include_unreachable=True), args.to)
        if not args.dry_run:
            post_to_socket(session["socket"], text)
        target = {"pid": session["pid"], "name": session["name"]}

    where = args.host or "this machine"
    name = target.get("name") or target.get("pid")
    # "Posted", not "delivered": the receiving session's inbound controls decide
    # whether Claude ever reads it. A session in bypassPermissions mode holds
    # every message for its user's approval unless crossSessionInbound is accept.
    verb = "Would post to" if args.dry_run else "Posted to"
    emit(
        args.json,
        {"ok": True, "target": target, "chars": len(text), "dryRun": args.dry_run},
        f"{verb} {name}'s inbox on {where} ({len(text)} chars).",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-peer",
        description="Message Claude Code sessions on another machine over SSH.",
    )
    parser.add_argument("--version", action="version", version=f"cc-peer {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--host", help="SSH destination; omit to act on this machine")
        sub.add_argument(
            "--ssh-opt",
            action="append",
            default=[],
            metavar="OPT",
            help="extra ssh argument, repeatable (e.g. --ssh-opt -p --ssh-opt 2222)",
        )
        sub.add_argument("--json", action="store_true", help="machine-readable output")

    listing = subparsers.add_parser("list", help="list sessions that can be messaged")
    add_common(listing)
    listing.add_argument(
        "--all", action="store_true", help="include stale records and sessions with no inbox"
    )
    listing.set_defaults(func=cmd_list)

    sending = subparsers.add_parser("send", help="send one message to a session")
    add_common(sending)
    sending.add_argument("--to", required=True, metavar="NAME|PID", help="target session")
    sending.add_argument("message", nargs="?", help="message text; omit or use - to read stdin")
    sending.add_argument("--b64", help=argparse.SUPPRESS)  # used for remote dispatch
    sending.add_argument(
        "--reply-to",
        metavar="HOST",
        help="reply address to advertise (default: this machine's tailnet address)",
    )
    sending.add_argument(
        "--no-reply-to", action="store_true", help="send without a reply address"
    )
    sending.add_argument(
        "--no-from", action="store_true", help="send without the From: header"
    )
    sending.add_argument("--dry-run", action="store_true", help="resolve the target, send nothing")
    sending.set_defaults(func=cmd_send)

    updating = subparsers.add_parser("update", help="update this installation")
    add_common(updating)
    updating.add_argument(
        "--check", action="store_true", help="report the available version, change nothing"
    )
    updating.set_defaults(func=cmd_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CcPeerError as exc:
        message = str(exc)
        if args.json:
            print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        else:
            print(f"cc-peer: {message}", file=sys.stderr)
        return EXIT_NO_TARGET if "no reachable session" in message else EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
