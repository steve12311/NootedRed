"""验证 VCN 最短开启时间，并可核对 Darwin 23–25 的原生驱动。"""
import argparse
from pathlib import Path
import re
import struct
import subprocess

from audit_video_driver import disk_image
from test_vcn_native_load import function

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn/min-on'


def native_fixture(path, index):
    symbols = {}
    output = subprocess.check_output(['nm', '-arch', 'x86_64', '-n', str(path)], text=True, timeout=60)
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16)
    with path.open('rb') as binary:
        thin_x86 = binary.read(4) == bytes.fromhex('cffaedfe')
    if not thin_x86:
        thin = WORK / f'native-{index}'
        subprocess.run(['lipo', str(path), '-thin', 'x86_64', '-output', str(thin)], check=True, timeout=60)
        path = thin
    _, read = disk_image(path)
    engine = 'AMDRadeonX6000_AMDVCN2HWEngine'
    table = symbols[f'__ZTV30{engine}'] + 16
    for slot, method in ((0x90, '4freeEv'),
                         (0x2D0, '12initHardwareE21eAMDAccelVideoChannel'),
                         (0x2D8, '15releaseHardwareE21eAMDAccelVideoChannel')):
        pointer, = struct.unpack('<Q', read(table + slot, 8))
        expected = symbols[f'__ZN30{engine}{method}']
        # 已提取的内核集合保留高位 fixup 元数据。
        assert pointer & 0xFFFFFFFFFFFF == expected & 0xFFFFFFFFFFFF, (path, method, hex(pointer), hex(expected))
    base = '__ZN29AMDRadeonX6000_AMDVCNHWEngine'
    stop = symbols[base+'9stopQueueE21eAMDAccelVideoChannel']
    suspend = symbols[base+'21setSuspendResumeStateEb']
    code = read(stop, suspend - stop)
    print(f'PASS native VCN slots: {path}', flush=True)
    return 'exercise({'+', '.join(str(b) for b in code)+'});'


HARNESS = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>

using UInt8 = uint8_t;
using UInt32 = uint32_t;
using UInt64 = uint64_t;
using mach_vm_address_t = uintptr_t;
using boolean_t = int;
constexpr int KERN_SUCCESS = 0;
constexpr UInt32 Unsupported = UINT32_MAX;
#define SYSLOG(...) ((void)0)

static bool writable, denyWrite, interrupts, atInterrupt, preemptible;
static UInt64 timeNs;
static unsigned hardwareInitCount, nativeReleases, nativeFrees;
static bool initResult;
static std::vector<UInt32> sleeps;
static void (*duringSleep)();
static UInt64 mach_absolute_time() { return timeNs; }
static void absolutetime_to_nanoseconds(UInt64 stamp, UInt64* result) { *result = stamp; }
static boolean_t ml_get_interrupts_enabled() { return interrupts; }
static boolean_t ml_at_interrupt_context() { return atInterrupt; }
static boolean_t canPreempt() { return preemptible; }
static void IOSleep(UInt32 ms) {
    sleeps.push_back(ms);
    timeNs += ms * 1000000ULL;
    if (duringSleep) {
        auto action = duringSleep;
        duringSleep = nullptr;
        action();
    }
}
struct KernelPatcher {
    static constexpr size_t KernelID = 0;
    static constexpr int kernelWriteLock = 0;
    std::map<std::string, uintptr_t> symbols;
    uintptr_t solveSymbol(size_t, const char* name, uintptr_t = 0, size_t = 0, bool = false) {
        return symbols[name];
    }
    void clearError() {}
};
struct MachInfo {
    static int setKernelWriting(bool value, int) {
        if (value && denyWrite) { return 1; }
        assert(writable != value);
        writable = value;
        return KERN_SUCCESS;
    }
};
struct NRed {
    unsigned properties = 0;
    static NRed& singleton() { static NRed red; return red; }
    void setProp32(const char*, UInt32) { ++properties; }
};
FUNCTIONS

static bool nativeInit(void*, UInt32) { return initResult; }
static bool nativeRelease(void*, UInt32) { ++nativeReleases; return false; }
static void nativeFree(void*) { ++nativeFrees; }

static void exercise(const std::vector<UInt8>& code) {
    alignas(uintptr_t) std::array<UInt8, 4096> memory{};
    auto base = reinterpret_cast<uintptr_t>(memory.data());
    auto table = base + 0x100;
    auto slots = reinterpret_cast<uintptr_t*>(table + 16);
    auto* freeSlot = &slots[FreeSlot / 8];
    auto* initSlot = &slots[InitSlot / 8];
    auto* releaseSlot = &slots[ReleaseSlot / 8];
    const auto stop = base + 0x600;
    const auto suspend = stop + code.size();
    const auto caller = stop + 0x36;
    KernelPatcher patcher;
    const auto reset = [&] {
        bridgedEngine = nullptr;
        engineVtable = stopQueueStart = stopQueueEnd = lastHardwareInit = 0;
        releaseHardwareOriginal = nullptr;
        minOnDelays = nativeReleases = nativeFrees = 0;
        interrupts = preemptible = initResult = true;
        denyWrite = atInterrupt = false;
        sleeps.clear();
        memory.fill(0);
        std::copy(code.begin(), code.end(), reinterpret_cast<UInt8*>(stop));
        *freeSlot = base + 0x500;
        *initSlot = base + 0x510;
        *releaseSlot = base + 0x520;
        patcher.symbols = {
            {"__ZTV30AMDRadeonX6000_AMDVCN2HWEngine", table},
            {"__ZN30AMDRadeonX6000_AMDVCN2HWEngine4freeEv", *freeSlot},
            {"__ZN30AMDRadeonX6000_AMDVCN2HWEngine12initHardwareE21eAMDAccelVideoChannel", *initSlot},
            {"__ZN30AMDRadeonX6000_AMDVCN2HWEngine15releaseHardwareE21eAMDAccelVideoChannel", *releaseSlot},
            {"__ZN29AMDRadeonX6000_AMDVCNHWEngine9stopQueueE21eAMDAccelVideoChannel", stop},
            {"__ZN29AMDRadeonX6000_AMDVCNHWEngine21setSuspendResumeStateEb", suspend},
            {"_ml_get_interrupts_enabled", reinterpret_cast<uintptr_t>(ml_get_interrupts_enabled)},
            {"_ml_at_interrupt_context", reinterpret_cast<uintptr_t>(ml_at_interrupt_context)},
            {"_preemption_enabled", reinterpret_cast<uintptr_t>(canPreempt)},
        };
    };
    const auto install = [&](bool disabled = false) {
        return installMinOnTime(patcher, 1, base, memory.size(), disabled);
    };

    reset();
    denyWrite = true;
    const auto unchanged = memory;
    assert(install() == Unsupported && unchanged == memory && !engineVtable && !writable);

    reset();
    ++*initSlot;
    const auto mismatched = memory;
    assert(install() == Unsupported && mismatched == memory && !engineVtable);

    reset();
    assert(install(true) == 0 && *releaseSlot == base + 0x520 && !releaseHardwareOriginal);

    reset();
    memory[caller - base - 1] ^= 1;
    assert(install() == Unsupported && *releaseSlot == base + 0x520 && !releaseHardwareOriginal);

    reset();
    patcher.symbols.erase("_preemption_enabled");
    assert(install() == Unsupported && *releaseSlot == base + 0x520);

    reset();
    ++*releaseSlot;
    assert(install() == Unsupported && *releaseSlot == base + 0x521);

    reset();
    assert(install() == MinOnMs && !writable);
    assert(*initSlot == reinterpret_cast<uintptr_t>(initHardware));
    assert(*releaseSlot == reinterpret_cast<uintptr_t>(releaseHardware));
    assert(*freeSlot == reinterpret_cast<uintptr_t>(freeEngine));
    assert(engineVtable == table + 16);
    initHardwareOriginal = nativeInit;
    releaseHardwareOriginal = nativeRelease;
    freeEngineOriginal = nativeFree;

    uintptr_t engine = engineVtable, otherEngine = engineVtable, wrongTable = 0;
    bindEngine(&wrongTable);
    assert(!bridgedEngine);
    bindEngine(&engine);
    bindEngine(&otherEngine);
    assert(bridgedEngine == &engine);

    timeNs = 80 * NsPerMs;
    assert(initHardware(&otherEngine, 8) && !lastHardwareInit);
    initResult = false;
    assert(!initHardware(&engine, 8) && !lastHardwareInit);
    assert(!waitForMinOn(&engine, 8, caller));
    initResult = true;
    assert(initHardware(&engine, 8) && lastHardwareInit == timeNs);

    timeNs = 79 * NsPerMs;
    assert(!waitForMinOn(&engine, 8, caller));
    initHardware(&engine, 9);
    assert(lastHardwareInit == 80 * NsPerMs);
    timeNs = 90 * NsPerMs - 1;
    assert(!waitForMinOn(&otherEngine, 8, caller));
    assert(!waitForMinOn(&engine, 7, caller));
    assert(!waitForMinOn(&engine, 11, caller));
    assert(!waitForMinOn(&engine, 8, stop));
    assert(!waitForMinOn(&engine, 8, caller + 1));
    assert(!waitForMinOn(&engine, 8, suspend + 0xE5));
    interrupts = false;
    assert(!waitForMinOn(&engine, 8, caller));
    interrupts = true;
    preemptible = false;
    assert(!waitForMinOn(&engine, 8, caller));
    preemptible = true;
    atInterrupt = true;
    assert(!waitForMinOn(&engine, 8, caller));
    atInterrupt = false;
    assert(sleeps.empty());

    duringSleep = [] { initHardware(bridgedEngine, 9); };
    assert(waitForMinOn(&engine, 8, caller));
    assert((sleeps == std::vector<UInt32>{41, 50}));
    assert(!waitForMinOn(&engine, 8, caller));
    assert(!releaseHardware(&engine, 8) && nativeReleases == 1);
    assert(!releaseHardware(&otherEngine, 11) && nativeReleases == 2);

    freeEngine(&otherEngine);
    assert(bridgedEngine == &engine && lastHardwareInit);
    freeEngine(&engine);
    assert(!bridgedEngine && !lastHardwareInit && nativeFrees == 2);
    bindEngine(&engine);
    assert(bridgedEngine == &engine && !waitForMinOn(&engine, 8, caller));
}

int main() {
    std::array<UInt8, 0x17A> synthetic{};
    std::copy(std::begin(StopReleaseCall), std::end(StopReleaseCall), synthetic.begin() + 0x30);
    exercise({synthetic.begin(), synthetic.end()});
    NATIVE_FIXTURES
    puts("PASS VCN minimum-on hooks, fallback, engine lifecycle and timing");
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--drivers', type=Path, nargs=3, metavar=('SONOMA', 'SEQUOIA', 'TAHOE'))
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'NootedRed/VCN.cpp').read_text()
    declarations = source[source.index('constexpr UInt32 MinOnMs'):source.index('    UInt64 nowNs()')]
    declarations = re.sub(r'(?m)^    ', '', declarations)
    resolve = 'template<typename T>\n' + function(source, 'bool resolve(')
    hooks = source[source.index('    UInt64 nowNs()'):source.index('    bool install(KernelPatcher')]
    native = '\n    '.join(native_fixture(path, index) for index, path in enumerate(args.drivers or []))
    harness = WORK / 'test.cpp'
    executable = WORK / 'test'
    harness.write_text(HARNESS.replace('FUNCTIONS', resolve+'\n'+declarations+hooks)
                       .replace('NATIVE_FIXTURES', native))
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(harness), '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == '__main__':
    main()
