"""Unit tests for cc-peer. Standard library only, no network, no SSH.

Run with:  python3 -m unittest test_cc_peer -v

Every case here corresponds to something that was once wrong and shipped —
the injection paths, the caps that weren't enforced, the reply line that
couldn't be run. The point is that the next release breaks loudly.
"""

import base64
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


if __name__ == "__main__":
    unittest.main()
