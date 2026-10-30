"""VPF：补丁站点审计的确定性与边界检查。"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/patch-audit'


class PatchAuditTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], check=False, timeout=60, capture_output=True, text=True)

    def test_audit_is_deterministic_and_bounded(self):
        WORK.mkdir(parents=True, exist_ok=True)
        first, second = WORK / 'audit-1.json', WORK / 'audit-2.json'
        markdown = WORK / 'audit.md'
        command = [str(ROOT / 'tools/vpf/audit_patch_sites.py'), '--json', str(first), '--markdown', str(markdown)]
        result = self._run(*command)
        self.assertEqual(result.returncode, 0, result.stderr)
        command[command.index(str(first))] = str(second)
        self.assertEqual(self._run(*command).returncode, 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        report = json.loads(first.read_text())
        self.assertEqual(report['kind'], 'vpf.patch-site-audit')
        self.assertEqual(report['totalSites'], 214)
        self.assertEqual(sum(report['totalByCategory'].values()), report['totalSites'])
        self.assertEqual(sum(report['totalByGroup'].values()), report['totalSites'])
        self.assertEqual(len(report['features']), 19)
        for candidate in report['reviewCandidates']:
            self.assertLessEqual(candidate['size'], 4)
            self.assertIn(candidate['group'], ('vcn-applegva', 'metal-display', 'submit-guard'))
        self.assertNotIn('interfaceAudit', report)
        self.assertNotIn('acceptanceMatrix', report)
        self.assertEqual(len(report['kernelAudit']), 3)
        self.assertTrue(all('markers' in item for item in report['kernelAudit']))
        self.assertTrue(markdown.read_text().startswith('# VPF 补丁站点审计'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
