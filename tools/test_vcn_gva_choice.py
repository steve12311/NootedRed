"""执行原生 AppleGVA 选择函数，验证 AMD-only 回退；不创建解码器、不访问 GPU。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from metal_cache import CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"
ADDRESS = 0x7FFA0C59BBAC
SIZE = 1136
SHA256 = "b7c725552045a9db206ab2eed4edda7ea3d27a29de2feee782204c362764a33d"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", action="store_true", help="用未修补分支执行回归，预期复现失败")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    cache = CacheReader()
    try:
        native = cache.read(ADDRESS, SIZE)
        assert hashlib.sha256(native).hexdigest() == SHA256
        patched = bytearray(native)
        if not args.stock:
            records = json.loads((WORK / "user-patches.json").read_text())["records"]
            record = next(r for r in records if r["label"] == "AppleGVA_AMDOnlyFallback")
            assert record["address"] == ADDRESS + 0x287
            assert bytes.fromhex(record["original"]) == native[0x287:0x28D]
            patched[0x287:0x28D] = bytes.fromhex(record["patched"])
        dump = WORK / "gva-choice-dump.s"
        dump.write_text(".text\n.globl _dump\n_dump:\n.byte " + ",".join(hex(b) for b in native) + "\n")
        obj = dump.with_suffix(".o")
        subprocess.run(["xcrun", "clang", "-c", str(dump), "-o", str(obj)], check=True, timeout=60)
        disasm = subprocess.run(["xcrun", "llvm-objdump", "-d", str(obj)], capture_output=True,
                                text=True, check=True, timeout=60).stdout
        calls = {0x1AB8: "mockPreference", 0x1613: "mockBoardID", 0x1534: "mockPush",
                 0xA2688: "strcmp", 0xA26BE: "mockLog", 0x7B3A2: "mockDefaultGPU",
                 0xA24C0: "free", 0xA241E: "mockFailure", 0xA2388: "mockFailure"}
        strings = {}
        assembly = [".text"]
        for label, code in [("stockChoice", native), ("candidateChoice", patched)]:
            assembly += [f".globl _{label}", f"_{label}:"]
            for line in disasm.splitlines():
                match = re.match(r"\s*([0-9a-f]+): ((?:[0-9a-f]{2}[ \t]+)+)(.*)", line)
                if not match:
                    continue
                offset, original, instruction = int(match[1], 16), bytes.fromhex(match[2]), match[3]
                raw = code[offset:offset + len(original)]
                if instruction.startswith("callq"):
                    target = int(re.search(r"0x([0-9a-f]+)", instruction)[1], 16)
                    assert len(raw) == 5 and raw[0] == 0xE8 and target in calls
                    assembly.append(f"callq _{calls[target]}")
                elif "(%rip)" in instruction:
                    target = int(re.search(r"## 0x([0-9a-f]+)", instruction)[1], 16)
                    if target == 0x33B0288C:
                        symbol = "guardPointer"
                    elif target == 0x36E7DF94:
                        symbol = "gpuHead"
                    else:
                        if target not in strings:
                            data = cache.read(ADDRESS + target, 160)
                            if data[8:12] in [b"\xc8\x07\0\0", b"\xd0\x07\0\0"]:
                                pointer, length = struct.unpack_from("<QQ", data, 16)
                                # 本机缓存的 slide v5 指针只在此离线 fixture 中解码。
                                pointer = cache.base + (pointer & ((1 << 34) - 1))
                                value = cache.read(pointer, length).decode()
                            else:
                                value = data.split(b"\0")[0].decode()
                            strings[target] = (f"gva_string_{len(strings)}", value)
                        symbol = strings[target][0]
                    assembly.append(re.sub(r"0x[0-9a-f]+\(%rip\)", f"_{symbol}(%rip)",
                                           instruction.split("##")[0].strip()))
                else:
                    assembly.append(".byte " + ",".join(hex(b) for b in raw))
        assembly.append(".section __TEXT,__cstring,cstring_literals")
        for symbol, value in strings.values():
            assembly += [f"_{symbol}:", ".asciz " + json.dumps(value)]
    finally:
        cache.close()
    asm = WORK / "gva-choice.s"
    asm.write_text("\n".join(assembly) + "\n")
    executable = WORK / "test-gva-choice"
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tools/fixtures/vcn-gva-choice.cpp"), str(asm), "-o", str(executable)],
                   check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == "__main__":
    main()
