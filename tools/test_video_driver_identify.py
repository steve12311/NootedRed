"""在 CPU 上执行核实过的原生 VA 识别函数；仅替换 IOKit 出口与全局缓冲地址。"""
from pathlib import Path
import hashlib
import subprocess

from audit_video_driver import symbols
from metal_cache import CACHE, CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"
EXPECTED = "d7c579bce55c5724ec241f7f9abece9d0f23b1c30178d2f539fc0cabf41dc0bd"


def byte_lines(data):
    return "\n".join(".byte " + ",".join(hex(v) for v in data[i:i + 16]) for i in range(0, len(data), 16))


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    cache = CacheReader()
    try:
        lines = (CACHE / "dyld_shared_cache_x86_64h.map").read_text().splitlines()
        image = "/System/Library/Extensions/AMDRadeonVADriver2.bundle/Contents/MacOS/AMDRadeonVADriver2"
        base = int(lines[lines.index(image) + 1].split()[1], 16)
        names, _ = symbols(cache.read, base)
        address = names["__ZN17VAAcceleratorInfo8identifyEjj"]
        end = min(a for a in names.values() if a > address)
        native = cache.read(address, end - address)
        if len(native) != 448 or hashlib.sha256(native).hexdigest() != EXPECTED:
            raise ValueError("VA 识别函数版本不匹配，拒绝执行")
        assert native[0x24:0x27] == b"\x48\x8d\x05"
        assert native[0x48] == 0xE8
        assert native[0x5A:0x5D] == b"\x0f\xb7\x05"
        # 三个替换点保持原指令长度，全部 PCI 判定和错误分支使用原生字节。
        assembly = ".text\n.globl _nativeIdentify\n_nativeIdentify:\n"
        assembly += byte_lines(native[:0x24]) + "\nleaq _fakeHardwareInfo(%rip), %rax\n"
        assembly += byte_lines(native[0x2B:0x48]) + "\ncallq _readHwInfo\n"
        assembly += byte_lines(native[0x4D:0x5A]) + "\nmovzwl _fakeHardwareInfo+4(%rip), %eax\n"
        assembly += byte_lines(native[0x61:]) + "\n"
    finally:
        cache.close()
    source = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
extern "C" {
alignas(8) uint8_t fakeHardwareInfo[0x118]{};
uint32_t nativeIdentify(uint32_t connection, uint32_t type);
static uint16_t deviceID;
static uint32_t ioStatus;
static unsigned calls;
uint32_t readHwInfo(uint32_t connection, uint32_t selector, const uint64_t* input, uint32_t inputCount,
                    const void* inputStruct, size_t inputSize, uint64_t* output, uint32_t* outputCount,
                    void* outputStruct, size_t* outputSize) {
    assert(connection == 0x1234 && selector == 0x100);
    assert(!input && !inputCount && !inputStruct && !inputSize && !output && !outputCount);
    assert(outputStruct == fakeHardwareInfo && *outputSize == sizeof(fakeHardwareInfo));
    memcpy(fakeHardwareInfo + 4, &deviceID, sizeof(deviceID));
    calls++;
    return ioStatus;
}
}
int main() {
    std::array<unsigned,12> counts{};
    for (unsigned id = 0; id < 65536; id++) {
        deviceID = id;
        ioStatus = 0;
        calls = 0;
        unsigned result = nativeIdentify(0x1234, 1);
        assert(calls == 1 && result < counts.size());
        counts[result]++;
        if (id == 0x1638 || id == 0x1636 || id == 0x15dd || id == 0x15d8) { assert(result == 11); }
        if (id == 0x731f) { assert(result == 5); }
        ioStatus = 0xe00002bc;
        assert(nativeIdentify(0x1234, 1) == 11);
        calls = 0;
        assert(nativeIdentify(0x1234, 0) == 11 && calls == 0);
    }
    printf("PASS: 65536 native PCI classifications, IOKit failures, and wrong-type refusal\n");
    printf("CONFIRMED: Cezanne/Renoir/Raven return unsupported enum 11; Navi10 0x731f returns enum 5\n");
    for (unsigned i = 0; i < counts.size(); i++) { printf("enum %u: %u devices\n", i, counts[i]); }
}
'''
    asm = WORK / "native-va-identify.s"
    cpp = WORK / "native-va-identify.cpp"
    executable = WORK / "test-native-va-identify"
    asm.write_text(assembly)
    cpp.write_text(source)
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-O2", str(cpp), str(asm), "-o", str(executable)],
                   check=True, timeout=60)
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=60)
    (WORK / "native-va-identify-results.txt").write_text(result.stdout)
    print(result.stdout, end="")


if __name__ == "__main__":
    main()
