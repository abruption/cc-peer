"""Unit tests for cc-peer. Standard library only, no network, no SSH.

Run with:  python3 -m unittest test_cc_peer -v

Every case here corresponds to something that was once wrong and shipped —
the injection paths, the caps that weren't enforced, the reply line that
couldn't be run. The point is that the next release breaks loudly.
"""

import argparse
import base64
import json
import os
import unittest
from unittest import mock

import cc_peer


class TailnetAddress(unittest.TestCase):
    def test_accepts_cgnat_range(self):
        for addr in ("100.64.0.1", "100.122.73.69", "100.127.255.255"):
            self.assertTrue(cc_peer.is_tailnet_address(addr), addr)

    def test_rejects_public_addresses_that_merely_start_with_100(self):
        # 100.200.x.x is ordinary public space; matching on "100." alone took it.
        for addr in ("100.200.1.1", "100.63.0.1", "100.128.0.1"):
            self.assertFalse(cc_peer.is_tailnet_address(addr), addr)

    def test_rejects_out_of_range_octets(self):
        for addr in ("100.64.999.999", "100.64.0.256", "100.64.0"):
            self.assertFalse(cc_peer.is_tailnet_address(addr), addr)

    def test_rejects_non_addresses(self):
        for addr in ("", "not-an-ip", "100.64.0.1.5", "100.64.0.x"):
            self.assertFalse(cc_peer.is_tailnet_address(addr), addr)


class SshArgumentChecks(unittest.TestCase):
    """--host and --ssh-opt reach ssh directly, so they are a trust boundary."""

    def test_host_may_not_start_with_a_dash(self):
        # ssh has no `--`, so a leading dash makes the host an option.
        with self.assertRaises(cc_peer.CcPeerError):
            cc_peer.check_ssh_argument("-oProxyCommand=touch /tmp/x", "--host")

    def test_rejects_options_that_run_a_local_command(self):
        for value in (
            "-oProxyCommand=whoami",
            "-o ProxyCommand=whoami",
            "-oPROXYCOMMAND=whoami",
            "-oLocalCommand=whoami",
            "-oPermitLocalCommand=yes",
        ):
            with self.assertRaises(cc_peer.CcPeerError, msg=value):
                cc_peer.check_ssh_argument(value, "--ssh-opt")

    def test_allows_ordinary_options(self):
        # ProxyJump takes a host, not a command — it is how you cross a bastion.
        for value in ("-p", "2222", "-oConnectTimeout=8", "-oProxyJump=bastion"):
            cc_peer.check_ssh_argument(value, "--ssh-opt")

    def test_allows_ordinary_hosts(self):
        for value in ("web-01", "ubuntu@10.0.0.4", "100.64.0.1"):
            cc_peer.check_ssh_argument(value, "--host")


class MessageChecks(unittest.TestCase):
    def test_rejects_empty_and_whitespace(self):
        for text in ("", "   ", "\n\n", "\t "):
            with self.assertRaises(cc_peer.CcPeerError, msg=repr(text)):
                cc_peer.check_message(text, remote=False)

    def test_local_cap(self):
        cc_peer.check_message("x" * cc_peer.MAX_MESSAGE_CHARS, remote=False)
        with self.assertRaises(cc_peer.CcPeerError):
            cc_peer.check_message("x" * (cc_peer.MAX_MESSAGE_CHARS + 1), remote=False)

    def test_remote_cap_is_lower_and_enforced(self):
        # Over SSH the body travels as an argv entry and meets MAX_ARG_STRLEN
        # long before the local cap.
        self.assertLess(cc_peer.MAX_REMOTE_MESSAGE_CHARS, cc_peer.MAX_MESSAGE_CHARS)
        oversized = "x" * (cc_peer.MAX_REMOTE_MESSAGE_CHARS + 1)
        cc_peer.check_message(oversized, remote=False)          # fine locally
        with self.assertRaises(cc_peer.CcPeerError) as caught:
            cc_peer.check_message(oversized, remote=True)
        self.assertIn(str(cc_peer.MAX_REMOTE_MESSAGE_CHARS), str(caught.exception))

    def test_remote_cap_leaves_room_for_base64(self):
        # base64 costs 4/3; the encoded form still has to fit in one argument.
        encoded = base64.b64encode(b"x" * cc_peer.MAX_REMOTE_MESSAGE_CHARS)
        self.assertLess(len(encoded), 128 * 1024)


class TargetResolution(unittest.TestCase):
    @staticmethod
    def session(pid, name, reachable=True):
        return {"pid": pid, "name": name, "reachable": reachable,
                "socket": f"/tmp/{pid}.sock", "alive": True}

    def test_resolves_by_name_and_pid(self):
        rows = [self.session(1, "alpha"), self.session(2, "beta")]
        self.assertEqual(cc_peer.resolve_target(rows, "beta")["pid"], 2)
        self.assertEqual(cc_peer.resolve_target(rows, "2")["name"], "beta")

    def test_name_match_ignores_case(self):
        rows = [self.session(1, "API-Worker")]
        self.assertEqual(cc_peer.resolve_target(rows, "api-worker")["pid"], 1)

    def test_names_with_spaces_survive(self):
        # These used to resolve as their first word, because the name reached
        # the remote shell unquoted.
        rows = [self.session(1, "my session")]
        self.assertEqual(cc_peer.resolve_target(rows, "my session")["pid"], 1)

    def test_unreachable_sessions_are_not_targets(self):
        rows = [self.session(1, "ghost", reachable=False)]
        with self.assertRaises(cc_peer.CcPeerError):
            cc_peer.resolve_target(rows, "ghost")

    def test_ambiguous_name_asks_for_a_pid(self):
        rows = [self.session(1, "twin"), self.session(2, "twin")]
        with self.assertRaises(cc_peer.CcPeerError) as caught:
            cc_peer.resolve_target(rows, "twin")
        self.assertIn("pid", str(caught.exception))

    def test_missing_name_lists_what_is_reachable(self):
        rows = [self.session(1, "alpha")]
        with self.assertRaises(cc_peer.CcPeerError) as caught:
            cc_peer.resolve_target(rows, "nosuch")
        self.assertIn("alpha", str(caught.exception))


class ReplyLine(unittest.TestCase):
    SESSION = {"pid": 42, "name": "documents-ed", "reachable": True}

    def line(self, host="100.64.0.1", session=SESSION, user="alice"):
        with mock.patch.object(cc_peer, "own_session", return_value=session), \
             mock.patch.object(cc_peer, "detect_reply_host", return_value=host), \
             mock.patch.object(cc_peer.getpass, "getuser", return_value=user), \
             mock.patch.dict(os.environ, {}, clear=True):
            return cc_peer.reply_line(None)

    def test_carries_user_absolute_path_and_no_reply_to(self):
        line = self.line()
        self.assertIn("alice@100.64.0.1", line)   # receiver connects as us, not itself
        self.assertIn("cc_peer.py", line)         # bare `cc-peer` isn't on a non-login PATH
        self.assertIn("--no-reply-to", line)      # an answer shouldn't invite an answer
        self.assertNotIn("~/.local/bin", line)

    def test_keeps_an_explicit_user(self):
        self.assertIn("bob@10.0.0.9", self.line(host="bob@10.0.0.9"))

    def test_quotes_a_name_with_spaces(self):
        line = self.line(session={"pid": 7, "name": "my session", "reachable": True})
        self.assertIn("'my session'", line)

    def test_falls_back_to_pid_when_unnamed(self):
        self.assertIn("--to 42", self.line(session={"pid": 42, "name": None,
                                                    "reachable": True}))

    def test_none_outside_a_session(self):
        with mock.patch.object(cc_peer, "own_session", return_value=None):
            self.assertIsNone(cc_peer.reply_line("100.64.0.1"))

    def test_none_when_no_address_can_be_found(self):
        with mock.patch.object(cc_peer, "own_session", return_value=self.SESSION), \
             mock.patch.object(cc_peer, "detect_reply_host", return_value=None), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(cc_peer.reply_line(None))


class Envelope(unittest.TestCase):
    """From: exists because Claude Code records socket-posted messages with
    origin.from = "unknown" — the receiver otherwise cannot tell who asked."""

    SESSION = {"pid": 42, "name": "documents-ed", "reachable": True}

    def wrap(self, body="hello", with_from=True, with_reply=True, host="100.64.0.1"):
        with mock.patch.object(cc_peer, "own_session", return_value=self.SESSION), \
             mock.patch.object(cc_peer, "detect_reply_host", return_value=host), \
             mock.patch.object(cc_peer.getpass, "getuser", return_value="alice"), \
             mock.patch.dict(os.environ, {}, clear=True):
            return cc_peer.wrap_message(body, None, with_from, with_reply)

    def test_default_carries_sender_and_reply(self):
        out = self.wrap()
        self.assertTrue(out.startswith("From: alice@100.64.0.1 (documents-ed)"))
        self.assertIn("hello", out)
        self.assertIn("Reply:", out)

    def test_from_survives_no_reply_to(self):
        # Knowing who sent something stays useful when you can't answer it.
        out = self.wrap(with_reply=False)
        self.assertIn("From: alice@100.64.0.1", out)
        self.assertNotIn("Reply:", out)

    def test_no_from_leaves_the_body_alone(self):
        self.assertEqual(self.wrap(with_from=False, with_reply=False), "hello")

    def test_does_not_repeat_what_claude_code_already_adds(self):
        # Claude Code prefaces peer messages and appends its own guidance;
        # duplicating either would compound with every hop.
        out = self.wrap()
        self.assertNotIn("Another Claude session", out)
        self.assertNotIn("permission laundering", out)

    def test_from_falls_back_to_hostname_without_a_tailnet_address(self):
        out = self.wrap(host=None)
        self.assertIn("From: alice@", out)
        self.assertNotIn("Reply:", out)   # nothing runnable to offer

    def test_no_envelope_outside_a_session(self):
        with mock.patch.object(cc_peer, "own_session", return_value=None):
            self.assertEqual(cc_peer.wrap_message("hello", None, True, True), "hello")

    def test_body_is_not_duplicated_or_reordered(self):
        out = self.wrap(body="line one\nline two")
        self.assertEqual(out.count("line one"), 1)
        self.assertLess(out.index("From:"), out.index("line one"))
        self.assertLess(out.index("line two"), out.index("Reply:"))


class VersionParsing(unittest.TestCase):
    def test_orders_releases(self):
        self.assertLess(cc_peer.parse_version("0.2.0"), cc_peer.parse_version("0.3.0"))
        self.assertLess(cc_peer.parse_version("v0.9.0"), cc_peer.parse_version("v0.10.0"))
        self.assertEqual(cc_peer.parse_version("v1.2.3"), cc_peer.parse_version("1.2.3"))

    def test_survives_junk_without_raising(self):
        # A malformed tag must not crash an update check; sorting lowest means
        # "don't offer this as newer".
        for text in ("", "not-a-version", "v..", "1.x.3"):
            self.assertIsInstance(cc_peer.parse_version(text), tuple)
        self.assertLess(cc_peer.parse_version("junk"), cc_peer.parse_version("0.0.1"))

    def test_ignores_anything_past_patch(self):
        self.assertEqual(cc_peer.parse_version("1.2.3.4"), (1, 2, 3))


class RemoteInstalledVersion(unittest.TestCase):
    """Why this exists at all: run_remote() ships and runs *our* source, so
    asking the remote command to report itself would only echo our version.
    The installed copy is the one that can fall behind."""

    def run_with(self, stdout):
        completed = mock.Mock(stdout=stdout, returncode=0)
        with mock.patch.object(cc_peer.subprocess, "run", return_value=completed):
            return cc_peer.remote_installed_version("web-01", [])

    def test_reads_the_reported_version(self):
        self.assertEqual(self.run_with("cc-peer 0.2.0\n"), "0.2.0")

    def test_none_when_not_installed(self):
        self.assertIsNone(self.run_with(""))
        self.assertIsNone(self.run_with("python3: can't open file\n"))

    def test_none_when_ssh_fails(self):
        with mock.patch.object(cc_peer.subprocess, "run", side_effect=OSError("boom")):
            self.assertIsNone(cc_peer.remote_installed_version("web-01", []))

    def test_still_validates_ssh_arguments(self):
        # This path builds its own ssh command, so it needs the same guard.
        with self.assertRaises(cc_peer.CcPeerError):
            cc_peer.remote_installed_version("-oProxyCommand=whoami", [])


class OwnSession(unittest.TestCase):
    def test_reads_pid_from_the_exported_socket_path(self):
        rows = [{"pid": 4011, "name": "worker", "reachable": True}]
        with mock.patch.dict(os.environ,
                             {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/4011.sock"}), \
             mock.patch.object(cc_peer, "discover", return_value=rows):
            self.assertEqual(cc_peer.own_session()["name"], "worker")

    def test_none_when_the_variable_is_absent(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(cc_peer.own_session())

    def test_none_when_the_path_has_no_pid(self):
        with mock.patch.dict(os.environ,
                             {"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/odd.sock"}):
            self.assertIsNone(cc_peer.own_session())


class ErrorHandling(unittest.TestCase):
    """Unhandled exceptions must not break the --json contract."""

    def test_main_catches_unexpected_exception_json(self):
        import io
        buf = io.StringIO()
        with mock.patch.object(cc_peer, "build_parser") as bp:
            ns = argparse.Namespace(func=mock.Mock(side_effect=RuntimeError("boom")), json=True)
            bp.return_value.parse_args.return_value = ns
            with mock.patch("builtins.print", side_effect=lambda *a, **kw: buf.write(a[0])):
                code = cc_peer.main(["list", "--json"])
        result = json.loads(buf.getvalue())
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])
        self.assertEqual(code, cc_peer.EXIT_ERROR)

    def test_main_catches_unexpected_exception_human(self):
        import io
        buf = io.StringIO()
        with mock.patch.object(cc_peer, "build_parser") as bp:
            ns = argparse.Namespace(func=mock.Mock(side_effect=RuntimeError("boom")), json=False)
            bp.return_value.parse_args.return_value = ns
            with mock.patch("builtins.print", side_effect=lambda *a, **kw: buf.write(a[0])):
                code = cc_peer.main(["list"])
        self.assertIn("boom", buf.getvalue())
        self.assertEqual(code, cc_peer.EXIT_ERROR)

    def test_discover_survives_permission_error(self):
        with mock.patch("pathlib.Path.is_dir", return_value=True), \
             mock.patch("pathlib.Path.glob", side_effect=PermissionError):
            result = cc_peer.discover()
        self.assertEqual(result, [])


class ReadMessage(unittest.TestCase):
    def test_rejects_tty_stdin(self):
        args = argparse.Namespace(b64=None, message=None)
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            with self.assertRaises(cc_peer.CcPeerError) as caught:
                cc_peer.read_message(args)
        self.assertIn("terminal", str(caught.exception))

    def test_catches_binary_stdin(self):
        args = argparse.Namespace(b64=None, message=None)
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.read.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
            with self.assertRaises(cc_peer.CcPeerError) as caught:
                cc_peer.read_message(args)
        self.assertIn("UTF-8", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
