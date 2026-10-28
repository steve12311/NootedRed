"""VPF：反汇编/CFG/liveness 变体比较、负样本与报告确定性。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/semantic'
sys.path.insert(0, str(ROOT / 'tools/vpf'))

import semantic_variants as sv


def _pad(code: bytes) -> bytes:
    return code + b'\x90' * (32 - len(code))


def _reference_code() -> bytes:
    return _pad(bytes.fromhex('55 48 89 e5 b8 78 56 34 12 83 c0 01 83 f8 04 75 03 31 c0 5d c3'))


def _renamed_code() -> bytes:
    return _pad(bytes.fromhex('55 48 89 e5 b9 78 56 34 12 83 c1 01 83 f9 04 75 03 31 c9 5d c3'))


def _reference(*, references=(), patches=()) -> sv.FunctionVariant:
    return sv.FunctionVariant('reference', 'AMDRadeonX5000GLDriver', _reference_code(),
                              references=tuple(references), patches=tuple(patches))


class SemanticTests(unittest.TestCase):
    def test_exact_and_register_renamed(self):
        exact = sv.compare_variants(_reference(), _reference())
        self.assertEqual((exact.status, exact.reason, exact.exact), (sv.ACCEPTED, 'exact', True))
        renamed = sv.compare_variants(_reference(), sv.FunctionVariant('renamed', 'AMDRadeonX5000GLDriver',
                                                                       _renamed_code()))
        self.assertEqual((renamed.status, renamed.reason), (sv.ACCEPTED, 'semantic_equivalent'))
        self.assertTrue(renamed.candidate)
        self.assertEqual(dict(renamed.register_mapping)['rax'], 'rcx')

    def test_unmasked_immediate_and_control_flow_rejected(self):
        changed = _reference_code()
        changed = changed[:6] + bytes([0x79]) + changed[7:]
        verdict = sv.compare_variants(_reference(), sv.FunctionVariant('immediate', 'x', changed))
        self.assertEqual((verdict.status, verdict.reason), (sv.REJECTED, 'immediate'))
        changed = _reference_code()
        changed = changed[:15] + bytes([0x75, 0x02]) + changed[17:]
        verdict = sv.compare_variants(_reference(), sv.FunctionVariant('branch', 'x', changed))
        self.assertEqual(verdict.status, sv.REJECTED)
        self.assertIn(verdict.reason, ('control_flow', 'instruction_count'))

    def test_indirect_random_and_misaligned_references_rejected(self):
        changed = _reference_code()
        changed = changed[:15] + bytes([0xFF, 0xE0]) + changed[17:]
        verdict = sv.compare_variants(_reference(), sv.FunctionVariant('indirect', 'x', changed))
        self.assertEqual(verdict.status, sv.REJECTED)
        random = bytes([0xFF]) * 32
        verdict = sv.compare_variants(_reference(), sv.FunctionVariant('random', 'x', random))
        self.assertEqual(verdict.status, sv.REJECTED)
        misaligned = sv.TypedReference(offset=6, target=0x1234)
        reference = _reference(references=(misaligned,))
        renamed = sv.FunctionVariant('renamed', 'x', _renamed_code(), references=(misaligned,))
        verdict = sv.compare_variants(reference, renamed)
        self.assertEqual(verdict.reason, 'reference_alignment')

    def test_layout_target_and_patch_changes_rejected(self):
        reference = _reference(references=(sv.TypedReference(offset=5, target=0x1234),))
        moved = _reference(references=(sv.TypedReference(offset=5, target=0x5678),))
        self.assertEqual(sv.compare_variants(reference, moved).reason, 'reference_target')
        candidate = sv.FunctionVariant('layout', 'x', _reference_code(),
                                       references=(sv.TypedReference(offset=5, target=0x1234),))
        self.assertEqual(sv.compare_variants(reference, candidate).status, sv.ACCEPTED)
        patched = _reference(patches=(sv.PatchConstraint(offset=12, size=3, original=bytes.fromhex('83 f8 04')),))
        changed = _reference_code()
        changed = changed[:12] + bytes.fromhex('83 f9 04') + changed[15:]
        other = sv.FunctionVariant('patch', 'x', changed,
                                   patches=(sv.PatchConstraint(offset=12, size=3,
                                                               original=bytes.fromhex('83 f9 04')),))
        self.assertEqual(sv.compare_variants(patched, other).reason, 'patch_site')

    def test_short_signature_and_multiple_candidates(self):
        with self.assertRaises(ValueError):
            sv.FunctionVariant('short', 'x', b'\x90' * 16)
        reference = _reference()
        selected, status = sv.select_unique(reference, [reference])
        self.assertEqual((selected, status), (reference, 'selected'))
        selected, status = sv.select_unique(reference, [reference, reference])
        self.assertEqual((selected, status), (None, 'ambiguous'))

    def test_json_round_trip(self):
        variant = _reference(references=(sv.TypedReference(offset=5, target=0x1234),))
        with tempfile.TemporaryDirectory(prefix='vpf-semantic-') as directory:
            path = Path(directory) / 'variants.json'
            path.write_text(json.dumps({'kind': 'vpf.semantic-variants',
                                        'variants': [sv.variant_to_json(variant)]}))
            loaded = sv.load_variants(path)
        self.assertEqual(loaded[0].code, variant.code)
        self.assertEqual(loaded[0].references, variant.references)


class ToolTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], check=False, timeout=120, capture_output=True, text=True)

    def test_manifest_report_is_deterministic_and_emits_nothing(self):
        WORK.mkdir(parents=True, exist_ok=True)
        first, second = WORK / 'manifest-1.json', WORK / 'manifest-2.json'
        markdown = WORK / 'manifest.md'
        command = [str(ROOT / 'tools/vpf/analyze_semantic_variants.py'), '--manifest', '--limit', '12',
                   '--json', str(first), '--markdown', str(markdown)]
        result = self._run(*command)
        self.assertEqual(result.returncode, 0, result.stderr)
        command[command.index(str(first))] = str(second)
        self.assertEqual(self._run(*command).returncode, 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        report = json.loads(first.read_text())
        self.assertEqual(report['kind'], 'vpf.semantic-report')
        self.assertEqual(report['emittedManifests'], 0)
        self.assertEqual(report['totalFeatureCount'], 21)
        self.assertEqual(report['variantCount'], 12)
        self.assertEqual(report['disassembled'] + report['disassemblyRejected'], report['variantCount'])
        self.assertGreater(report['disassembled'], 0)
        self.assertTrue(markdown.read_text().startswith('# VPF 语义变体证据报告'))

    def test_pair_cli_accepts_and_rejects(self):
        WORK.mkdir(parents=True, exist_ok=True)
        reference = WORK / 'reference.json'
        accepted = WORK / 'accepted.json'
        rejected = WORK / 'rejected.json'
        reference.write_text(json.dumps({'variants': [sv.variant_to_json(_reference())]}))
        accepted.write_text(json.dumps({'variants': [sv.variant_to_json(
            sv.FunctionVariant('renamed', 'x', _renamed_code()))]}))
        changed = _reference_code()
        changed = changed[:6] + bytes([0x79]) + changed[7:]
        rejected.write_text(json.dumps({'variants': [sv.variant_to_json(sv.FunctionVariant('bad', 'x', changed))]}))
        command = [str(ROOT / 'tools/vpf/analyze_semantic_variants.py'), '--reference', str(reference),
                   '--candidate', str(accepted), '--json', str(WORK / 'pair.json')]
        self.assertEqual(self._run(*command).returncode, 0)
        report = json.loads((WORK / 'pair.json').read_text())
        self.assertEqual(report['verdict']['status'], sv.ACCEPTED)
        self.assertEqual(report['emittedManifests'], 0)
        command[command.index(str(accepted))] = str(rejected)
        command.insert(-2, '--fail-on-rejected')
        self.assertEqual(self._run(*command).returncode, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
