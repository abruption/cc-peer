"""Codex contract tests: fixture DBs and process boundaries, no live agents."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import session_peer as peer

THREAD = "01900000-0000-7000-8000-000000000001"
OTHER = "01900000-0000-7000-8000-000000000002"


class Codex(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / "state_5.sqlite"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE threads (id TEXT, name TEXT, title TEXT, cwd TEXT, updated_at INTEGER, archived INTEGER, rollout_path TEXT)")
        conn.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?)", [
            (THREAD, None, "title", "/project", 10, 0, "/missing-rollout"),
            (OTHER, "named", "fallback", "/other", 20, 1, "/missing-rollout")])
        conn.commit()
        conn.close()
        self.args = argparse.Namespace(codex_home=str(self.root), codex_bin=None, all=False,
                                       to="codex:" + THREAD, dry_run=False)

    def invoke(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = peer.main(list(argv))
        return code, json.loads(output.getvalue())

    def test_readonly_discovery_filters_and_sorts_saved_sessions(self):
        before = self.db.read_bytes()
        rows = peer.discover_codex(self.args)
        self.assertEqual([r["id"] for r in rows], [THREAD])
        self.assertEqual(rows[0]["name"], "title")
        self.assertNotIn("alive", rows[0])
        self.assertNotIn("reachable", rows[0])
        self.args.all = True
        self.assertEqual([r["id"] for r in peer.discover_codex(self.args)], [OTHER, THREAD])
        self.assertEqual(before, self.db.read_bytes())

    def test_missing_db_does_not_create_it(self):
        self.args.codex_home = str(self.root / "missing")
        with self.assertRaisesRegex(peer.CcPeerError, "not found"):
            peer.discover_codex(self.args)
        self.assertFalse((self.root / "missing").exists())

    def test_title_preview_does_not_dump_transcripts(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE threads SET title=? WHERE id=?", ("x" * 200 + "\nprivate transcript", THREAD))
            conn.commit()
        self.assertEqual(peer.discover_codex(self.args)[0]["name"], "x" * 120)

    def test_changed_schema_is_actionable(self):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DROP TABLE threads")
            conn.commit()
        with self.assertRaisesRegex(peer.CcPeerError, "Unsupported Codex"):
            peer.discover_codex(self.args)

    def test_home_precedence(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "env")}):
            self.assertEqual(peer.codex_home(self.args), self.root.resolve())
            self.args.codex_home = None
            self.assertEqual(peer.codex_home(self.args), (self.root / "env").resolve())

    def test_invalid_ids_fail_before_dispatch(self):
        for target in ("codex:", "codex:short", "codex:../data", "codex:" + THREAD + ";echo x"):
            with mock.patch.object(peer, "run_remote") as run:
                code, result = self.invoke("send", "--host", "worker", "--to", target, "--json", "test")
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])
            run.assert_not_called()

    def test_cli_missing(self):
        with mock.patch.object(peer.shutil, "which", return_value=None):
            with self.assertRaisesRegex(peer.CcPeerError, "--codex-bin"):
                peer.queue_codex(self.args, "test")

    def test_queue_argv_home_and_payload(self):
        message = "한글 🧪\n'quotes' \"double\" $HOME `literal` $(literal);|&"
        done = subprocess.CompletedProcess([], 0, "Queued message test-id for thread " + THREAD + ".\n", "")
        with mock.patch.object(peer, "codex_executable", return_value="/path with space/codex"), \
             mock.patch.object(peer.subprocess, "run", return_value=done) as run:
            result = peer.queue_codex(self.args, message)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["queueId"], "test-id")
        self.assertEqual(run.call_args.args[0], ["/path with space/codex", "queue", "--thread", THREAD, "--message", message])
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], str(self.root.resolve()))
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_count, 1)

    def test_different_success_output_remains_success(self):
        with mock.patch.object(peer, "codex_executable", return_value="codex"), \
             mock.patch.object(peer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "accepted", "")):
            result = peer.queue_codex(self.args, "test")
        self.assertEqual(result["status"], "queued")
        self.assertNotIn("queueId", result)

    def test_dry_run_checks_saved_id_without_running(self):
        self.args.dry_run = True
        with mock.patch.object(peer, "codex_executable", return_value="codex"), \
             mock.patch.object(peer.subprocess, "run") as run:
            result = peer.queue_codex(self.args, "test")
        self.assertEqual(result["status"], "validated")
        run.assert_not_called()

    def test_unknown_dry_run_target_exit_code(self):
        with mock.patch.object(peer, "codex_executable", return_value="codex"):
            code, result = self.invoke("send", "--to", "codex:" + "00000000-0000-0000-0000-000000000000",
                                       "--codex-home", str(self.root), "--dry-run", "--no-from", "--no-reply-to", "--json", "test")
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])

    def test_timeout_is_unknown_and_never_retried(self):
        with mock.patch.object(peer, "codex_executable", return_value="codex"), \
             mock.patch.object(peer.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 30)) as run:
            with self.assertRaisesRegex(peer.CcPeerError, "outcome unknown"):
                peer.queue_codex(self.args, "test")
        self.assertEqual(run.call_count, 1)

    def test_failure_is_not_success(self):
        for error in ("no rollout found", "attempt to write a readonly database"):
            with mock.patch.object(peer, "codex_executable", return_value="codex"), \
                 mock.patch.object(peer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", error)):
                with self.assertRaisesRegex(peer.CcPeerError, error):
                    peer.queue_codex(self.args, "test")

    def test_byte_limit_after_wrapping(self):
        peer.check_codex_message("한" * 10922 + "ab")
        with self.assertRaisesRegex(peer.CcPeerError, "UTF-8 bytes"):
            peer.check_codex_message("한" * 10923)
        with self.assertRaisesRegex(peer.CcPeerError, "NUL"):
            peer.check_codex_message("a\x00b")
        with mock.patch.object(peer, "wrap_message", return_value="x" * 32769), \
             mock.patch.object(peer, "queue_codex") as queue:
            code, _ = self.invoke("send", "--to", "codex:" + THREAD, "--json", "short")
        self.assertEqual(code, 1)
        queue.assert_not_called()

    def test_remote_send_preserves_status_and_destination_options(self):
        response = {"ok": True, "target": {"agent": "codex", "id": THREAD}, "status": "queued",
                    "queueId": "queue-id", "dryRun": False, "chars": 4}
        with mock.patch.object(peer, "run_remote", return_value=response) as remote:
            code, result = self.invoke("send", "--host", "worker", "--to", "codex:" + THREAD,
                "--codex-home", "/remote home", "--codex-bin", "/remote bin/codex", "--json", "--no-from", "--no-reply-to", "test")
        self.assertEqual(code, 0)
        self.assertEqual(result["host"], "worker")
        self.assertEqual(result["queueId"], "queue-id")
        self.assertEqual(result["status"], "queued")
        argv = remote.call_args.args[1]
        self.assertEqual(argv[argv.index("--codex-home") + 1], "/remote home")
        self.assertEqual(argv[argv.index("--codex-bin") + 1], "/remote bin/codex")

    def test_remote_list_forwards_agent_and_home(self):
        with mock.patch.object(peer, "run_remote", return_value={"sessions": []}) as remote, \
             mock.patch.object(peer, "remote_installed_version", return_value=None):
            code, result = self.invoke("list", "--agent", "codex", "--host", "worker", "--all", "--codex-home", "/remote", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(result[0]["host"], "worker")
        self.assertEqual(remote.call_args.args[1], ["list", "--all", "--agent", "codex", "--codex-home", "/remote"])


if __name__ == "__main__":
    unittest.main()
