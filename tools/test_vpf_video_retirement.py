"""VPF：Video 生产路径静态收敛检查。"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'NootedRed/UserVideoDecode.cpp'


class VideoRetirementTests(unittest.TestCase):
    def test_video_production_path_uses_vpf_chain(self):
        text = SOURCE.read_text()
        for token in (
            'VPF::ManifestData::find',
            'VPF::adaptPatches',
            'VPF::plan(',
            'VPF::VerifiedPatchPlan',
            'VPF::SharedRegionTransaction',
            'NRedVCNVPFPath',
            'NRedVCNVPFQuarantined',
            'installPlan',
            'wrappedMapDestroy',
            '"_vm_map_destroy"',
            'removeLedgerMap',
        ):
            self.assertIn(token, text)
        for feature in (
            'Vcn2Decode',
            'Vcn2Encode',
            'AppleGVASelection',
            'FairPlayNativeDecodeSubset',
        ):
            self.assertIn(feature, text)

    def test_old_video_orchestration_is_gone(self):
        text = SOURCE.read_text()
        for token in ('UserLegacyData', 'legacyProfile', 'combined[', 'memmove('):
            self.assertNotIn(token, text)
        # 编解码拦截器已移除：不得重新引入守卫解析、守卫页或私有入口补丁。
        for token in ('UserVideoGuard', 'createGuard(', 'recordSafety(', 'guard.plan', 'adaptGuard',
                      'Vcn2SafetyGuard'):
            self.assertNotIn(token, text)
        # 只通过 ExecutorOps 传递 copyout，不直接调用。
        self.assertEqual(text.count('writeUser('), 0)

    def test_native_hook_and_map_gate_order_preserved(self):
        text = SOURCE.read_text()
        self.assertIn('"_shared_region_check_np"', text)
        self.assertIn('"_vm_map_destroy"', text)
        hook = text[text.index('int wrappedSharedRegionCheck') : text.index('}    // namespace')]
        original = hook.index('originalCheck)(process, args, returnValue)')
        lease = hook.index('UserPatchMapGate::Lease')
        patch = hook.index('patchSharedRegion(process, args, result)')
        self.assertLess(original, lease)
        self.assertLess(lease, patch)
        self.assertIn('prepareLedger(mapKey)', text)
        self.assertIn('NRedVCNVPFQuarantined', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
