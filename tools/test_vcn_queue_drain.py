"""验证 VCN 队列等待、原生释放顺序和实际驱动的 ABI 匹配。"""
import argparse
from pathlib import Path
import subprocess

from audit_video_driver import disk_image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vcn/queue-drain'


def native_sources(path, version, work):
    names = {}
    result = subprocess.run(['nm', '-arch', 'x86_64', '-n', str(path)], capture_output=True,
                            check=True, text=True, timeout=60)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3:
            names[fields[2]] = int(fields[0], 16)
    with path.open('rb') as binary:
        if binary.read(4) != bytes.fromhex('cffaedfe'):
            thin = work / f'AMDRadeonX{version}'
            subprocess.run(['lipo', str(path), '-thin', 'x86_64', '-output', str(thin)], check=True, timeout=60)
            path = thin
    _, read = disk_image(path)
    wait = names[f'__ZN30AMDRadeonX{version}_AMDAccelChannel16waitForTimestampEj']
    return names, read, wait


def native_test(old, new, index):
    names, read, wait = native_sources(old, 5000, WORK)
    replay = names['__ZN30AMDRadeonX5000_AMDAccelChannel22resetHardwareAndReplayEv']
    data = {'replay': read(replay, 0x280)}
    old_delta = wait - replay
    names, read, wait = native_sources(new, 6000, WORK)
    stop = names['__ZN29AMDRadeonX6000_AMDVCNHWEngine9stopQueueE21eAMDAccelVideoChannel']
    suspend = names['__ZN29AMDRadeonX6000_AMDVCNHWEngine21setSuspendResumeStateEb']
    data.update(stop=read(stop, 0x100), suspend=read(suspend, 0x1CA))
    arrays = '\n'.join(f'UInt8 {key}[]{{{", ".join(f"0x{b:02x}" for b in raw)}}};' for key, raw in data.items())
    return f'''
    {{
        {arrays}
        const auto target = [](const UInt8* base, intptr_t delta) {{
            return reinterpret_cast<const UInt8*>(reinterpret_cast<uintptr_t>(base) + delta);
        }};
        VCNQueueDrain::Layout actual{{}};
        assert(VCNQueueDrain::readStop(stop, sizeof(stop), actual));
        assert(VCNQueueDrain::readSuspend(suspend, sizeof(suspend), target(suspend, {wait - suspend}), actual));
        assert(VCNQueueDrain::readsSubmittedStamp(replay, sizeof(replay), target(replay, {old_delta}), actual.submitted));
        assert(actual.users[1] == actual.users[0] + 4);
        assert(!VCNQueueDrain::readStop(stop, 0x20, actual));
        stop[0x32] ^= 1;
        assert(!VCNQueueDrain::readStop(stop, sizeof(stop), actual));
        assert(!VCNQueueDrain::readSuspend(suspend, sizeof(suspend), target(suspend, {wait - suspend + 1}), actual));
        assert(!VCNQueueDrain::readsSubmittedStamp(replay, sizeof(replay), target(replay, {old_delta}), actual.submitted + 4));
        puts("PASS native driver pair {index}");
    }}
'''


HARNESS = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <VCNQueueDrain.hpp>

using mach_vm_address_t = uintptr_t;
struct OSMetaClass { unsigned tag; };
struct OSMetaClassBase {
    uintptr_t* table;
    unsigned tag;
    UInt32 stamp;
    unsigned references = 0;
    void retain() { ++references; }
    void release() { assert(references); --references; }
};
using OSObject = OSMetaClassBase;
using IOService = OSMetaClassBase;
struct IOWorkLoop {
    bool held = false, busy = false;
    unsigned entered = 0, left = 0, references = 0;
    bool inGate() const { return held; }
    void retain() { ++references; }
    void release() { assert(references); --references; }
protected:
    virtual bool tryCloseGate() {
        if (busy) { return false; }
        assert(!held);
        ++entered;
        return held = true;
    }
    virtual void openGate() {
        assert(held);
        ++left;
        held = false;
    }
} workLoop;

struct Engine {
    UInt8 padding[0x10]{};
    UInt8 enabled{1};
    IOService* accelerator;
    OSMetaClassBase* channels[3];
    UInt32 users[2]{1, 1};
};
static VCNQueueDrain::Layout queueDrainLayout{
    {offsetof(Engine, users), offsetof(Engine, users) + sizeof(UInt32)},
    offsetof(Engine, accelerator), {offsetof(Engine, channels), offsetof(Engine, channels) + sizeof(void*),
                                  offsetof(Engine, channels) + 2 * sizeof(void*)},
    0x120, 0x1A0, offsetof(OSMetaClassBase, stamp)
};
OSMetaClass acceleratorClass{1}, channelClass{2}, wrongClass{3};
struct { OSMetaClass* oldClass; } pairs[3]{{&acceleratorClass}, {&wrongClass}, {&channelClass}};
void* queueDrainEngine = nullptr;
mach_vm_address_t stopQueueOriginal, safeCastOriginal, accelLookup, wait5000;
UInt32 hardwareWorkLoopOwner = 0;
IOWorkLoop* (*hardwareWorkLoop)(const IOService*) = [](const IOService*) { return &workLoop; };

FUNCTIONS

static OSMetaClassBase channels[3]{{nullptr, 2, 5}, {nullptr, 2, 13}, {nullptr, 2, 21}};
static bool completion = true, nativeResult = true, callerGated = false, nativeGated = false, compete = false;
static unsigned advances = 0;
static std::vector<UInt32> calls;

int main() {
    uintptr_t acceleratorTable[0x1A0 / sizeof(void*) + 1]{};
    uintptr_t hardwareTable[0x120 / sizeof(void*) + 1]{};
    IOService accelerator{acceleratorTable, 1, 0};
    OSMetaClassBase hw[3]{{hardwareTable, 0, 0}, {hardwareTable, 1, 0}, {hardwareTable, 2, 0}};
    Engine engine, other;
    engine.accelerator = &accelerator;
    for (unsigned i = 0; i < 3; ++i) { engine.channels[i] = &hw[i]; }
    queueDrainEngine = &engine;
    safeCastOriginal = reinterpret_cast<uintptr_t>(+[](const OSMetaClassBase* obj, const OSMetaClass* type) {
        return obj && obj->tag == type->tag ? const_cast<OSMetaClassBase*>(obj) : nullptr;
    });
    accelLookup = reinterpret_cast<uintptr_t>(+[](void*, UInt32 index) -> OSMetaClassBase* {
        return index < 3 ? &channels[index] : nullptr;
    });
    acceleratorTable[0x1A0 / sizeof(void*)] = accelLookup;
    hardwareTable[0x120 / sizeof(void*)] = reinterpret_cast<uintptr_t>(+[](void* obj) -> SInt32 {
        return static_cast<SInt32>(static_cast<OSMetaClassBase*>(obj)->tag);
    });
    wait5000 = reinterpret_cast<uintptr_t>(+[](void* obj, UInt32 stamp) {
        assert(workLoop.held == callerGated);
        auto* channel = static_cast<OSMetaClassBase*>(obj);
        assert(channel->stamp == stamp && channel->references == 1);
        calls.push_back(stamp);
        if (advances) { ++channel->stamp; --advances; }
        if (compete) { workLoop.busy = true; }
        return completion;
    });
    stopQueueOriginal = reinterpret_cast<uintptr_t>(+[](void*, UInt32 channel) {
        calls.push_back(100 + channel);
        nativeGated = workLoop.held;
        return nativeResult;
    });
    const auto expect = [&](void* target, UInt32 channel, std::vector<UInt32> expected) {
        calls.clear();
        assert(stopQueue(target, channel) == nativeResult);
        assert(calls == expected);
        assert(!workLoop.references && !accelerator.references);
        for (const auto& targetChannel : channels) { assert(!targetChannel.references); }
    };
    expect(&other, 8, {108});
    expect(&engine, 12, {112});
    assert(workLoop.entered == 0);
    engine.users[0] = 2;
    expect(&engine, 8, {108});
    engine.users[0] = 1;
    engine.enabled = 0;
    expect(&engine, 8, {108});
    engine.enabled = 1;
    expect(&engine, 8, {5, 108});
    assert(nativeGated);
    expect(&engine, 10, {13, 21, 110});
    expect(&engine, 9, {13, 21, 109});
    workLoop.busy = true;
    expect(&engine, 8, {108});
    workLoop.busy = false;
    channels[1].tag = 3;
    expect(&engine, 9, {109});
    channels[1].tag = 2;
    completion = false;
    nativeResult = false;
    expect(&engine, 8, {5, 108});
    assert(!nativeGated);
    completion = true;
    advances = 1;
    expect(&engine, 8, {5, 6, 108});
    assert(nativeGated);
    advances = 2;
    expect(&engine, 8, {6, 7, 108});
    assert(!nativeGated);
    compete = true;
    expect(&engine, 8, {8, 108});
    compete = false;
    workLoop.busy = false;
    assert(!workLoop.held && workLoop.entered == workLoop.left);
    callerGated = workLoop.held = true;
    expect(&engine, 8, {8, 108});
    assert(workLoop.held && workLoop.entered == workLoop.left);
    puts("PASS VCN drain order, engine scope, final user, encode pair, busy gate, stale timestamp, ABI and timeout fallback");
    NATIVE
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native', nargs=2, type=Path, action='append', default=[], metavar=('X5000', 'X6000'))
    args = parser.parse_args()
    stubs = WORK / 'stubs'
    (stubs / 'IOKit').mkdir(parents=True, exist_ok=True)
    (stubs / 'libkern').mkdir(exist_ok=True)
    (stubs / 'IOKit/IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using SInt32=int32_t;\n')
    (stubs / 'libkern/libkern.h').write_text('#pragma once\n#include <cstring>\n')
    source = (ROOT / 'NootedRed/VCN.cpp').read_text()
    start = source.index('    template<typename T>\n    const T& queueField(')
    code = source[start:source.index('    void prepareQueueDrain5000(', start)]
    native = ''.join(native_test(old, new, i) for i, (old, new) in enumerate(args.native, 1))
    cpp = WORK / 'test.cpp'
    cpp.write_text(HARNESS.replace('FUNCTIONS', code).replace('NATIVE', native))
    exe = WORK / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I'+str(stubs), '-I'+str(ROOT / 'NootedRed'), str(cpp),
                    '-o', str(exe)], check=True, timeout=60)
    subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == '__main__':
    main()
