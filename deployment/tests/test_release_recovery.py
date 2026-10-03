"""Fault injection around backups, migrations and release state transitions."""
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_autodeploy import module


@unittest.skipUnless(os.name == "posix", "Deployment runs on Linux")
class ReleaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "deployment"
        self.config.mkdir()
        (self.config / ".env").write_text("TEST_CONFIG=preserved\nAPP_VERSION=old\n")
        (self.root / "repository.git").mkdir()
        self.old_sha, self.sha = "a" * 40, "b" * 40
        self.old = self.root / "releases" / self.old_sha / "deployment"
        self.old.mkdir(parents=True)
        (self.old / "compose.yaml").write_text("services: {}\n")
        self.state = {"sha": self.old_sha}
        (self.config / "deployed.json").write_text(json.dumps(self.state))
        (self.root / "current").symlink_to(self.old.parent, target_is_directory=True)
        self.calls = []
        self.script = module("deploy")

    def run_release(self, failure=None, empty_backup=False):
        def command(arguments, **kwargs):
            self.calls.append(arguments)
            if arguments[-2:] == ["rev-parse", "refs/heads/deploy"]:
                return self.sha + "\n"
            if "archive" in arguments:
                path = Path(arguments[arguments.index("-o") + 1])
                with tarfile.open(path, "w") as archive:
                    payload = b"services: {}\n"
                    member = tarfile.TarInfo("deployment/compose.yaml")
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
            if failure == "migration" and "upgrade" in arguments:
                raise subprocess.CalledProcessError(1, arguments)
            if failure == "health" and arguments[0] == "curl":
                raise subprocess.CalledProcessError(22, arguments)

        def backup(arguments, **kwargs):
            self.calls.append(arguments)
            if not empty_backup:
                kwargs["stdout"].write(b"non-empty-test-database-backup")

        with patch.object(self.script, "ROOT", self.root), patch.object(self.script, "CONFIG", self.config), \
             patch.object(self.script, "run", side_effect=command), \
             patch.object(self.script.subprocess, "run", side_effect=backup):
            self.script.deploy(self.sha)

    def test_backup_precedes_migration_and_success_promotes_release(self):
        self.run_release()
        backup_index = next(i for i, command in enumerate(self.calls) if "pg_dump" in " ".join(command))
        migration_index = next(i for i, command in enumerate(self.calls) if "upgrade" in command)
        self.assertLess(backup_index, migration_index)
        self.assertFalse(any("seed" in command or "down" in command for command in self.calls))
        state = json.loads((self.config / "deployed.json").read_text())
        self.assertEqual(state["sha"], self.sha)
        self.assertTrue(Path(state["backup"]).stat().st_size)
        self.assertEqual((self.root / "current").resolve(), self.root / "releases" / self.sha)
        self.assertIn("TEST_CONFIG=preserved", (self.root / "current/deployment/.env").read_text())

    def test_empty_backup_stops_before_migrations(self):
        with self.assertRaisesRegex(RuntimeError, "backup is empty"):
            self.run_release(empty_backup=True)
        self.assertFalse(any("upgrade" in command for command in self.calls))
        self.assertEqual(json.loads((self.config / "deployed.json").read_text()), self.state)

    def test_failed_migration_and_health_restore_previous_api_without_reversing_db(self):
        for failure in ("migration", "health"):
            with self.subTest(failure=failure):
                self.calls.clear()
                with self.assertRaises(subprocess.CalledProcessError):
                    self.run_release(failure=failure)
                recovery = self.calls[-1]
                self.assertIn(str(self.old / "compose.yaml"), recovery)
                self.assertIn("up", recovery)
                self.assertFalse(any("downgrade" in command for command in self.calls))
                self.assertEqual(json.loads((self.config / "deployed.json").read_text()), self.state)
                self.assertEqual((self.root / "current").resolve(), self.old.parent)
