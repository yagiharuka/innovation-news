"""Execute the workflow's probe shell offline, with a fake Python executable."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/url-audit.yml'


def workflow_step(name):
    text = WORKFLOW.read_text()
    start = text.index('      - name: ' + name + '\n')
    end = text.find('\n      - name:', start + 1)
    return text[start:end if end >= 0 else len(text)]


def shell_from_step(name):
    step = workflow_step(name)
    run = step.split('        run: |\n', 1)[1]
    return '\n'.join(line[10:] for line in run.splitlines() if line.startswith('          '))


class AuditIndependenceTests(unittest.TestCase):
    def test_all_ten_probes_run_and_write_outputs_even_after_failures(self):
        script = shell_from_step('Exercise OECD and stabilized collector paths')
        fake = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
source = args[args.index('--source') + 1]
output = pathlib.Path(args[args.index('--output') + 1])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({'source': source, 'new_probe': True}))
with pathlib.Path('calls.jsonl').open('a') as handle:
    handle.write(json.dumps(source) + '\\n')
raise SystemExit(1 if source in json.loads(os.environ['FAIL_SOURCES']) else 0)
'''
        for failures in [[], ['OECD Newsroom'], ['OECD Newsroom', 'OECD STI Publications & Indicators', 'ITER News']]:
            with self.subTest(failures=failures), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                executable = temp / 'bin/python'
                executable.parent.mkdir()
                executable.write_text(fake)
                executable.chmod(0o755)
                stale = temp / 'data/source_probes/stale.json'
                stale.parent.mkdir(parents=True)
                stale.write_text('{"status":"old_success"}')
                env = dict(os.environ, PATH=str(executable.parent) + os.pathsep + os.environ['PATH'], FAIL_SOURCES=json.dumps(failures))
                result = subprocess.run(['bash', '-c', script], cwd=temp, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1 if failures else 0, result.stderr)
                calls = [json.loads(line) for line in (temp / 'calls.jsonl').read_text().splitlines()]
                self.assertEqual(len(calls), 10)
                self.assertEqual(calls[-1], 'ITER News')
                self.assertFalse(stale.exists())
                outputs = list((temp / 'data').rglob('*.json'))
                self.assertEqual(len(outputs), 10)
                self.assertTrue(all(json.loads(path.read_text())['new_probe'] for path in outputs))

    def test_audit_and_probes_aggregate_after_always_uploading_artifacts(self):
        text = WORKFLOW.read_text()
        for name in ['Audit every active source endpoint and published article URL', 'Exercise OECD and stabilized collector paths']:
            self.assertIn('continue-on-error: true', workflow_step(name))
        upload = workflow_step('Upload full audit report')
        self.assertIn('if: always()', upload)
        self.assertIn('data/source_probes/*.json', upload)
        self.assertLess(text.index('name: Upload full audit report'), text.index('name: Require healthy audit and all source probes'))
        self.assertLess(text.index('name: Require healthy audit and all source probes'), text.index('name: Commit audit report'))
        script = shell_from_step('Require healthy audit and all source probes')
        for audit, probe in [('success', 'success'), ('failure', 'success'), ('success', 'failure'), ('skipped', 'success')]:
            result = subprocess.run(['bash', '-c', script], env=dict(os.environ, AUDIT_OUTCOME=audit, PROBE_OUTCOME=probe))
            self.assertEqual(result.returncode, 0 if audit == probe == 'success' else 1)
        probes = workflow_step('Exercise OECD and stabilized collector paths')
        self.assertEqual(probes.count('--min-seen 1 --min-kept 1'), 2)


if __name__ == '__main__':
    unittest.main()
