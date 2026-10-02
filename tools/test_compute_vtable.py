"""只读核对本机驱动虚表，并编译实际修正代码验证槽位、失败分支及提交返回值。"""

from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vtable-baseline"
DRIVER = Path("/System/Library/Extensions/AMDRadeonX5000.kext/Contents/MacOS/AMDRadeonX5000")


def main():
    disk = DRIVER.read_bytes()
    assert struct.unpack_from("<I", disk)[0] == 0xFEEDFACF
    commands = struct.unpack_from("<I", disk, 16)[0]
    cursor, segments = 32, []
    for _ in range(commands):
        command, size = struct.unpack_from("<II", disk, cursor)
        if command == 0x19:
            segments.append(struct.unpack_from("<QQQQ", disk, cursor + 24))
        cursor += size

    def read(address, size):
        for start, length, offset, file_size in segments:
            if start <= address and address + size <= start + min(length, file_size):
                return disk[offset + address - start:offset + address - start + size]
        raise ValueError(f"地址超出文件映射：{address:#x}")

    result = subprocess.run(["nm", "-n", str(DRIVER)], check=True, capture_output=True, text=True, timeout=60)
    symbols = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3:
            try:
                symbols[fields[2]] = int(fields[0], 16)
            except ValueError:
                continue
    table = symbols["__ZTV39AMDRadeonX5000_AMDGFX9PM4ComputeChannel"]
    submit = symbols["__ZN30AMDRadeonX5000_AMDPM4HWChannel19submitCommandBufferEP30AMD_SUBMIT_COMMAND_BUFFER_INFO"]
    space = symbols["__ZN27AMDRadeonX5000_AMDHWChannel21checkCommandRingSpaceEj"]
    assert struct.unpack("<Q", read(table + 0x188, 8))[0] == space
    assert struct.unpack("<Q", read(table + 16 + 0x188, 8))[0] == submit
    assert space != submit

    source = (ROOT / "NootedRed/X5000.cpp").read_text()
    start = source.index("        // __ZTV 符号含")
    end = source.index("\n    }", start)
    hook = source[start:end]
    start = source.index("UInt32 X5000::computeSubmitCommandBuffer(")
    end = source.index("\n}\n", start) + 3
    wrapper = source[start:end]
    harness = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <stdexcept>
using UInt8 = uint8_t;
using UInt32 = uint32_t;
using mach_vm_address_t = uintptr_t;
static bool failWrite = false;
static unsigned writes = 0, notifyCalls = 0, submitCalls = 0;
static UInt32 submitResult = 0;
static void* expectedSelf;
static void* expectedInfo;
struct MachInfo {
    static int setKernelWriting(bool enabled, int) {
        if (enabled && failWrite) { return 1; }
        writes++;
        return 0;
    }
};
struct KernelPatcher { static constexpr int kernelWriteLock = 0; };
constexpr int KERN_SUCCESS = 0;
#define SYSLOG(...) ((void)0)
#define PANIC_COND(condition, ...) do { if (condition) throw std::runtime_error("write denied"); } while (0)
#define FunctionCast(name, address) reinterpret_cast<decltype(&name)>(address)
struct Field {
    size_t offset;
    uintptr_t& operator()(void* p) { return *reinterpret_cast<uintptr_t*>(static_cast<UInt8*>(p) + offset); }
};
struct X5000 {
    Field hwChannelSubmitCommandBuffer{0x188};
    Field hwChannelHWInterfaceField{0x20};
    uintptr_t orgPM4SubmitCommandBuffer = 0;
    void (*notifyGfxAccess)(uintptr_t) = nullptr;
    static X5000& singleton() { static X5000 instance; return instance; }
    static UInt32 computeSubmitCommandBuffer(void*, void*);
    void install(void* pm4ComputeChannelVT) {
HOOK
    }
};
WRAPPER
static void notify(uintptr_t value) { assert(value == 0x1234); notifyCalls++; }
static UInt32 submitBuffer(void* self, void* info) {
    assert(notifyCalls == submitCalls + 1);
    assert(self == expectedSelf && info == expectedInfo);
    submitCalls++;
    return submitResult;
}
int main() {
    auto& module = X5000::singleton();
    for (size_t offset : {0x188U, 0x190U}) {
        module.hwChannelSubmitCommandBuffer.offset = offset;
        std::array<uintptr_t, 80> table;
        for (size_t i = 0; i < table.size(); i++) { table[i] = 0x1000 + i; }
        module.orgPM4SubmitCommandBuffer = table[(offset + 16) / 8];
        const auto original = module.orgPM4SubmitCommandBuffer;
        const auto before = table;
        writes = 0;
        module.install(table.data());
        assert(writes == 2 && module.orgPM4SubmitCommandBuffer == original);
        for (size_t i = 0; i < table.size(); i++) {
            assert(table[i] == (i == (offset + 16) / 8 ? reinterpret_cast<uintptr_t>(X5000::computeSubmitCommandBuffer)
                                                       : before[i]));
        }
        table = before;
        module.orgPM4SubmitCommandBuffer = 0xBADC0DE;
        writes = 0;
        module.install(table.data());
        assert(table == before && writes == 0 && module.orgPM4SubmitCommandBuffer == 0xBADC0DE);
        module.orgPM4SubmitCommandBuffer = original;
        failWrite = true;
        bool rejected = false;
        try { module.install(table.data()); } catch (const std::runtime_error&) { rejected = true; }
        assert(rejected && table == before && writes == 0);
        failWrite = false;
    }
    std::array<uintptr_t, 8> channel{};
    channel[4] = 0x1234;
    expectedSelf = channel.data();
    expectedInfo = reinterpret_cast<void*>(0x5678);
    module.orgPM4SubmitCommandBuffer = reinterpret_cast<uintptr_t>(submitBuffer);
    module.notifyGfxAccess = notify;
    for (UInt32 value : {0U, 0xE00002BDU}) {
        submitResult = value;
        assert(X5000::computeSubmitCommandBuffer(expectedSelf, expectedInfo) == value);
    }
    assert(notifyCalls == 2 && submitCalls == 2);
}
'''
    WORK.mkdir(parents=True, exist_ok=True)
    test = WORK / "test-compute-vtable.cpp"
    test.write_text(harness.replace("HOOK", hook).replace("WRAPPER", wrapper))
    executable = WORK / "test-compute-vtable"
    subprocess.run(["clang++", "-std=c++23", "-O2", str(test), "-o", str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)
    print("PASS: native vtable confirms wrong checkCommandRingSpace slot; actual hook changes only submit slot")
    print("PASS: mismatch/write failure preserve table; notify ordering and native submit status preserved")


if __name__ == "__main__":
    main()
