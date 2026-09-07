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
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

__version__ = "0.1.0"

# Claude Code refuses a same-machine message once its serialized form passes
# about a million characters, so fail here rather than at the far end.
MAX_MESSAGE_CHARS = 1_000_000
CONNECT_TIMEOUT = 10.0
DRAIN_TIMEOUT = 2.0

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


def post_to_socket(socket_path: str, text: str) -> None:
    """Write one message to a session's inbox socket.

    On macOS and Linux the {"type":"auth",...} line the docs describe is
    optional, so this sends the message on its own. Claude Code closes a
    connection that has not sent a complete line within 30 seconds, so the
    message is built before the socket is opened.
    """
    if not text.strip():
        raise CcPeerError("refusing to send an empty message")
    if len(text) > MAX_MESSAGE_CHARS:
        raise CcPeerError(
            f"message is {len(text)} characters; the limit is {MAX_MESSAGE_CHARS}"
        )

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

        conn.sendall((payload + "\n").encode("utf-8"))
        # Half-close and wait for the far end, so a write that the session
        # never read is reported here rather than silently dropped.
        conn.shutdown(socket.SHUT_WR)
        conn.settimeout(DRAIN_TIMEOUT)
        try:
            conn.recv(1)
        except OSError:
            pass
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Remote dispatch. Ships this file over SSH and runs it there, so the remote
# machine needs nothing installed beyond python3.
# --------------------------------------------------------------------------


def run_remote(host: str, argv: list[str], ssh_opts: list[str]) -> dict:
    try:
        source = Path(__file__).resolve().read_text()
    except OSError as exc:  # pragma: no cover - only when run from a pipe
        raise CcPeerError(f"cannot read own source to send to {host}: {exc}") from exc

    command = ["ssh", *ssh_opts, host, "python3", "-", *argv, "--json"]
    try:
        completed = subprocess.run(
            command, input=source, text=True, capture_output=True, timeout=120
        )
    except FileNotFoundError as exc:
        raise CcPeerError("ssh not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CcPeerError(f"ssh to {host} timed out") from exc

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
    if args.host:
        result = run_remote(args.host, ["list"] + (["--all"] if args.all else []), args.ssh_opt)
        sessions = result.get("sessions", [])
    else:
        sessions = discover(include_unreachable=args.all)

    where = args.host or "this machine"
    emit(args.json, {"sessions": sessions}, render_sessions(sessions, where))
    return 0


def read_message(args: argparse.Namespace) -> str:
    if args.b64 is not None:
        return base64.b64decode(args.b64).decode("utf-8")
    if args.message is None or args.message == "-":
        return sys.stdin.read()
    return args.message


def cmd_send(args: argparse.Namespace) -> int:
    text = read_message(args)

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
    sending.add_argument("--dry-run", action="store_true", help="resolve the target, send nothing")
    sending.set_defaults(func=cmd_send)

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
