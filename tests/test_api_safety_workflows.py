import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SafetyCheckpointWorkflowTests(unittest.TestCase):
    def test_all_api_workflows_preserve_failed_attempt_usage_before_exiting(self):
        for name in ("daily.yml", "weekly.yml", "review-backlog.yml", "review-history.yml", "oecd-recovery.yml"):
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn("id: api_batch", text)
                self.assertIn("if: always() && ", text)
                self.assertIn("steps.api_batch.outcome == 'failure'", text)
                self.assertIn("bash scripts/persist_api_safety.sh", text)
        recovery = (ROOT / ".github/workflows/oecd-recovery.yml").read_text()
        self.assertIn("steps.coverage.outcome == 'failure'", recovery)

    def test_failure_persistence_commits_only_safety_files_not_public_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "data").mkdir()
            (root / "data/run_log.json").write_text("{}")
            (root / "data/review_state.json").write_text("{}")
            fake_git = root / "bin/git"
            fake_git.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GIT_TEST_LOG"
if [[ "$1" == diff ]]; then exit 1; fi
''')
            fake_git.chmod(0o755)
            log = root / "git.log"
            env = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                       GIT_TEST_LOG=str(log))
            subprocess.run(["bash", str(ROOT / "scripts/persist_api_safety.sh")], cwd=root, env=env, check=True)
            commands = log.read_text().splitlines()
            self.assertIn("add -- data/run_log.json data/review_state.json", commands)
            self.assertIn("commit --only -m data: persist API safety checkpoint -- data/run_log.json data/review_state.json", commands)
            self.assertIn("pull --rebase --autostash origin main", commands)
            self.assertEqual(commands[-1], "push")
            self.assertNotIn("docs", log.read_text())
