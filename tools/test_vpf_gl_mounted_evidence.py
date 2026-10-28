"""DVPF：挂载卷只读采集、参考块匹配、位置迁移和负样本。"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/gl-evidence'
_SYSTEM_ROOT = os.environ.get('NRED_GL_SYSTEM_ROOT')
SEQUOIA = Path(_SYSTEM_ROOT) if _SYSTEM_ROOT else None
sys.path.insert(0, str(ROOT / 'tools/vpf'))
sys.path.insert(0, str(ROOT / 'tools/dvpf'))

import gl_evidence
import display_data


def _run_tool(root: Path, out: Path, *, verify: bool = True) -> subprocess.CompletedProcess:
    command = [sys.executable, str(ROOT / 'tools/vpf/build_gl_evidence.py'),
               '--system-root', str(root), '--out-dir', str(out)]
    if verify:
        command.append('--verify')
    command.append('--emit')
    return subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)


def _copy_system_root(transform=None) -> tuple[Path, Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=WORK, prefix='root-'))
    version = root / 'System/Library/CoreServices/SystemVersion.plist'
    driver = root / gl_evidence.DRIVER_PATH
    version.parent.mkdir(parents=True, exist_ok=True)
    driver.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SEQUOIA / 'System/Library/CoreServices/SystemVersion.plist', version)
    data = bytearray((SEQUOIA / gl_evidence.DRIVER_PATH).read_bytes())
    if transform:
        transform(data)
    driver.write_bytes(data)
    driver.chmod(0o644)
    return root, driver


def _move_block(data: bytearray, profile: dict) -> None:
    block, _, block_offset, _ = gl_evidence._fixture_block(profile, 0)
    refs = gl_evidence._fixture_refs(0, profile['major'], False)
    old_offset = profile['hw']['address'] + 0x128 - profile['hw']['tailOffset']
    new_block_offset = 0x900
    page = old_offset & ~4095
    absolute = page + new_block_offset
    moved = bytearray(block)
    for ref in refs:
        relative = ref['offset'] - 0x128
        displacement = ref['rva'] - (absolute + relative + 4)
        struct.pack_into('<i', moved, relative, displacement)
    data[page + (old_offset & 4095):page + (old_offset & 4095) + len(block)] = b'\0' * len(block)
    data[page + new_block_offset:page + new_block_offset + len(moved)] = moved


def _flip_block_byte(data: bytearray, profile: dict) -> None:
    block, _, _, _ = gl_evidence._fixture_block(profile, 0)
    offset = profile['hw']['address'] + 0x128
    data[offset] ^= 0x01


class MountedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SEQUOIA is not None and not SEQUOIA.is_dir():
            raise ValueError('NRED_GL_SYSTEM_ROOT 不是有效目录')
        cls.profile = gl_evidence.load_fixture_profiles()[24]
        cls.evidence = gl_evidence.build_fixture_evidence()

    def test_fixture_header_is_current(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/vpf/build_gl_evidence.py'),
                                 '--fixtures', '--check'], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(SEQUOIA is not None, '需要设置 NRED_GL_SYSTEM_ROOT')
    def test_real_volume_is_deterministic_and_compatible(self):
        WORK.mkdir(parents=True, exist_ok=True)
        first = WORK / 'real-1'
        second = WORK / 'real-2'
        self.assertEqual(_run_tool(SEQUOIA, first).returncode, 0)
        self.assertEqual(_run_tool(SEQUOIA, second).returncode, 0)
        for name in ('gl-proof.json', 'gl-variants.json', 'gl-manifest-data.json'):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        proof = json.loads((first / 'gl-proof.json').read_text())
        self.assertEqual(proof['source']['darwin'], 24)
        self.assertTrue(proof['source']['productVersion'].startswith('15.'))
        self.assertEqual(len(proof['source']['fileSHA256']), 64)
        self.assertTrue(proof['allReferenceBlocksFound'])
        self.assertEqual([entry['status'] for entry in proof['evidence']], ['matched', 'matched'])
        for entry in proof['evidence']:
            self.assertEqual(entry['candidateCount'], 1)
            self.assertTrue(entry['runtimeCompatible'])
            self.assertTrue(entry['referencePageMatched'])
            self.assertTrue(all(ref['referenceTargetMatched'] for ref in entry['refs']))
            self.assertTrue(all(ref['targetInFile'] for ref in entry['refs']))
        manifest = json.loads((first / 'gl-manifest-data.json').read_text())
        self.assertEqual(len(manifest['entries']), 12)
        self.assertEqual({entry['deviceID'] for entry in manifest['entries']},
                         set(display_data.supported_devices()))
        expected = {(item['major'], item['deviceID'], item['kind']): item for item in self.evidence}
        for entry in manifest['entries']:
            reference = expected[(entry['major'], entry['deviceID'], entry['kind'])]
            for key in ('pageFileOffset', 'blockOffsetInPage', 'pageDigest', 'originalDigest', 'patchedDigest'):
                self.assertEqual(entry[key], reference[key], key)
        variants = json.loads((first / 'gl-variants.json').read_text())
        self.assertEqual(variants['emittedManifests'], 0)
        self.assertEqual(len(variants['oldNewEquivalence']), 12)

    @unittest.skipUnless(SEQUOIA is not None, '需要设置 NRED_GL_SYSTEM_ROOT')
    def test_moved_block_is_still_a_dynamic_candidate(self):
        root, _ = _copy_system_root(lambda data: _move_block(data, self.profile))
        out = WORK / 'moved'
        result = _run_tool(root, out)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads((out / 'gl-proof.json').read_text())
        hw = next(entry for entry in proof['evidence'] if entry['kind'] == 0)
        dual = next(entry for entry in proof['evidence'] if entry['kind'] == 1)
        self.assertEqual(hw['status'], 'matched')
        self.assertTrue(hw['runtimeCompatible'])
        self.assertFalse(hw['referencePageMatched'])
        self.assertEqual(hw['blockOffsetInPage'], 0x900)
        self.assertEqual(hw['candidateCount'], 1)
        self.assertEqual(dual['status'], 'matched')
        self.assertTrue(proof['allReferenceBlocksFound'])

    @unittest.skipUnless(SEQUOIA is not None, '需要设置 NRED_GL_SYSTEM_ROOT')
    def test_non_reference_block_is_rejected(self):
        root, _ = _copy_system_root(lambda data: _flip_block_byte(data, self.profile))
        out = WORK / 'mutated'
        result = _run_tool(root, out)
        self.assertEqual(result.returncode, 2, result.stderr)
        proof = json.loads((out / 'gl-proof.json').read_text())
        hw = next(entry for entry in proof['evidence'] if entry['kind'] == 0)
        self.assertEqual(hw['status'], 'missing')
        self.assertFalse(hw['runtimeCompatible'])
        self.assertFalse(proof['allReferenceBlocksFound'])

    def test_symlink_escape_is_rejected(self):
        WORK.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(dir=WORK, prefix='escape-'))
        version = root / 'System/Library/CoreServices/SystemVersion.plist'
        version.parent.mkdir(parents=True, exist_ok=True)
        version.symlink_to('/etc/hosts')
        result = _run_tool(root, WORK / 'escape', verify=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('符号链接逃逸', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
