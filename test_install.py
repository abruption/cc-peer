"""Isolated standalone installation and coexistence checks; no real HOME writes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipIf(os.name == "nt", "POSIX installer; Windows uses pip")
class Install(unittest.TestCase):
    def test_install_reinstall_and_remove_preserve_legacy(self):
        with tempfile.TemporaryDirectory(prefix="session-peer-test-") as directory:
            root = Path(directory)
            legacy = root / ".claude/skills/cc-peer/cc_peer.py"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy", encoding="utf-8")
            env = dict(os.environ, HOME=str(root), CLAUDE_CONFIG_DIR=str(root / "custom claude"))
            script = str(Path(__file__).parent / "install.sh")
            for _ in range(2):
                result = subprocess.run(["sh", script], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((root / ".local/bin/session-peer").is_symlink())
                self.assertTrue((root / ".local/share/session-peer/session_peer.py").is_file())
                self.assertTrue((root / "custom claude/skills/session-peer/SKILL.md").is_file())
            result = subprocess.run(["sh", script, "--uninstall"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / ".local/bin/session-peer").exists())
            self.assertFalse((root / ".local/share/session-peer").exists())
            self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy")
