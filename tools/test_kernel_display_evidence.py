"""DVPF：内核显示只读证据、Mach-O 采集与热路径边界。"""
from __future__ import annotations

import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vpf/kernel-display-evidence'
sys.path.insert(0, str(ROOT / 'tools/vpf'))


def _compiler() -> list[str]:
    result = subprocess.run(['xcrun', '--find', 'clang++'], capture_output=True, text=True, timeout=10)
    return ['xcrun', 'clang++'] if result.returncode == 0 else ['clang++']


def _harness() -> str:
    return r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <IOKit/IOTypes.h>
#include <NRed.hpp>
#include <KernelDisplayEvidence.hpp>
#include <KernelDisplayEvidence.cpp>

static std::vector<UInt8> macho(UInt64 source, UInt8 seed) {
    std::vector<UInt8> bytes(72);
    auto put = [&](UInt32 offset, UInt32 value) { memcpy(bytes.data() + offset, &value, 4); };
    put(0, 0xFEEDFACF); put(4, 0x01000007); put(8, 3); put(12, 6); put(16, 2); put(20, 40);
    put(32, 0x1B); put(36, 24);
    for (UInt32 i = 0; i < 16; ++i) bytes[40 + i] = UInt8(seed + i);
    put(56, 0x2A); put(60, 16);
    memcpy(bytes.data() + 64, &source, 8);
    return bytes;
}

int main() {
    using namespace KernelDisplayEvidence;
    Properties::clear();
    resetForTest();
    auto valid = macho(0x1122334455667788ULL, 0x20);
    captureKext(Kext::X5000, reinterpret_cast<UInt64>(valid.data()), valid.size());
    recordGFX9(0x1638, 0x80, 0x91, 0xA1, 0x1C, 0x123456789ULL);
    recordFramebuffer(24, true, false, false);
    recordComputeVTable(true);
    publish();
    assert(Properties::map().at("NRedKernelKextValid") == 1);
    assert(Properties::map().at("NRedKernelKextError") == 0);
    assert(Properties::map().at("NRedKernelX5000KextSize") == valid.size());
    assert(Properties::map().at("NRedKernelX5000KextUUID0") == 0x23222120U);
    assert(Properties::map().at("NRedKernelX5000KextUUID1") == 0x27262524U);
    assert(Properties::map().at("NRedKernelX5000SourceVersionLow") == 0x55667788U);
    assert(Properties::map().at("NRedKernelX5000SourceVersionHigh") == 0x11223344U);
    assert(Properties::map().at("NRedKernelGFX9DeviceID") == 0x1638);
    assert(Properties::map().at("NRedKernelGFX9Revisions") == (0x80U | (0x91U << 8) | (0xA1U << 16)));
    assert(Properties::map().at("NRedKernelGFX9Attributes") == 0x1C);
    assert(Properties::map().at("NRedKernelFramebufferDarwin") == 24);
    assert(Properties::map().at("NRedKernelFramebufferFlags") == 1);
    assert(Properties::map().at("NRedKernelComputeVTable") == 1);

    // 无效 Mach-O 必须清除对应 valid 位并记录错误，而不是伪造成已采集。
    resetForTest();
    std::vector<UInt8> invalid(32, 0);
    captureKext(Kext::X6000FB, reinterpret_cast<UInt64>(invalid.data()), invalid.size());
    publish();
    assert((Properties::map().at("NRedKernelKextValid") & 2U) == 0);
    assert((Properties::map().at("NRedKernelKextError") & 2U) != 0);

    // 只有状态变化才返回 true；同一模式重复调用不增加发布次数。
    resetForTest();
    assert(recordScanout(0, 32, 8, 8, 3, 0));
    assert(!recordScanout(0, 32, 8, 8, 3, 0));
    assert(recordScanout(0, 32, 10, 10, 3, 0));
    publish();
    assert(Properties::map().at("NRedKernelScanoutChanges") == 2);
    assert(recordFlipParameters(0, 8, 1920, 0));
    assert(!recordFlipParameters(0, 8, 1920, 0));
    assert(recordFlipParameters(0, 8, 2560, 0));
    assert(!recordFlipWait(0, 5));
    assert(recordFlipWait(0, 12));
    assert(!recordFlipWait(0, 0));
    publish();
    assert(Properties::map().at("NRedKernelFlipWaitCount") == 3);
    assert(Properties::map().at("NRedKernelFlipWaitMax") == 12);
    assert(Properties::map().at("NRedKernelFlipWaitTotal") == 17);
    printf("PASS kernel display evidence: Mach-O UUID/version, invalid rejection, change-only scanout/flip waits\n");
}
'''


def _build() -> Path:
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n'
    )
    (WORK / 'stubs/NRed.hpp').write_text(
        '#pragma once\n#include <IOKit/IOTypes.h>\n#include <map>\n#include <string>\n'
        'struct Properties {\n'
        '    static std::map<std::string, UInt32>& map() {\n'
        '        static std::map<std::string, UInt32> value; return value;\n'
        '    }\n'
        '    static void clear() { map().clear(); }\n'
        '};\n'
        'class NRed {\n'
        'public:\n'
        '    static NRed& singleton() { static NRed value; return value; }\n'
        '    void setProp32(const char* key, UInt32 value) const { Properties::map()[key] = value; }\n'
        '};\n'
    )
    source = WORK / 'probe.cpp'
    source.write_text(_harness())
    executable = WORK / 'probe'
    subprocess.run(_compiler() + [
        '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-DKERNEL_DISPLAY_EVIDENCE_HOST',
        '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(executable),
    ], check=True, timeout=120)
    return executable


class KernelDisplayEvidenceTests(unittest.TestCase):
    def test_probe_passes(self):
        result = subprocess.run([str(_build())], check=True, capture_output=True, text=True, timeout=120)
        self.assertIn('PASS', result.stdout)

    def test_sanitized_probe_passes(self):
        executable = WORK / 'probe-sanitize'
        source = WORK / 'probe.cpp'
        subprocess.run(_compiler() + [
            '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-DKERNEL_DISPLAY_EVIDENCE_HOST',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'), str(source), '-o', str(executable),
        ], check=True, timeout=120)
        result = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=120)
        self.assertIn('PASS', result.stdout)

    def test_production_integration(self):
        nred = (ROOT / 'NootedRed/NRed.cpp').read_text()
        x5000 = (ROOT / 'NootedRed/X5000.cpp').read_text()
        x6000 = (ROOT / 'NootedRed/X6000FB.cpp').read_text()
        dcn = (ROOT / 'NootedRed/AMDGFX9DCNDisplay.cpp').read_text()
        evidence = (ROOT / 'NootedRed/KernelDisplayEvidence.cpp').read_text()
        project = (ROOT / 'NootedRed.xcodeproj/project.pbxproj').read_text()
        for text in (nred, x5000, x6000, dcn):
            self.assertIn('KernelDisplayEvidence', text)
        self.assertIn('recordGFX9', nred)
        self.assertIn('captureKext(KernelDisplayEvidence::Kext::X5000', x5000)
        self.assertIn('recordComputeVTable', x5000)
        self.assertIn('captureKext(KernelDisplayEvidence::Kext::X6000FB', x6000)
        self.assertIn('recordFramebuffer', x6000)
        self.assertIn('recordScanout', dcn)
        self.assertIn('recordFlipParameters', dcn)
        self.assertIn('recordFlipWait', dcn)
        self.assertIn('while (self->isFlipPending(self, fbIndex))', dcn)
        self.assertNotIn('HDRIntegerFilter', evidence)
        self.assertNotIn('isFlipPending', evidence)
        self.assertIn('KernelDisplayEvidence.cpp in Sources', project)
        self.assertIn('KernelDisplayEvidence.cpp', project)
        self.assertIn('KernelDisplayEvidence.hpp', project)


if __name__ == '__main__':
    unittest.main(verbosity=2)
