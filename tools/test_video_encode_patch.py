"""执行原生编码消费者的 CPU 回归；先运行 build_video_decode_patch.py 生成补丁数据。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from audit_video_driver import symbols
from build_video_decode_patch import HASHES
from metal_cache import CACHE, CacheReader

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn-encode-patch"
PITCH = "__ZN11Vcn2Encoder15setEncodeParamsEPvP15VAVendorTexture"
SIZE = "__ZN14Vcn2EncCommand15getFeedbackSizeEv"
AVC = "__ZN26AVDVcn2AvcEncDataProcessor19ProcessEncodeStatusEP19_VAEncodeStatusInfoPvP15VAVendorTexture"
HEVC = "__ZN27AVDVcn2HevcEncDataProcessor19ProcessEncodeStatusEP19_VAEncodeStatusInfoPvP15VAVendorTexture"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    report = json.loads((ROOT / "build/vcn/user-patches.json").read_text())
    rows = [r for r in report["records"] if r["label"] in [PITCH, SIZE, AVC, HEVC]]
    assert len(rows) == 4
    assembly = ".text\n"
    cache = CacheReader()
    try:
        lines = (CACHE / "dyld_shared_cache_x86_64h.map").read_text().splitlines()
        image = "/System/Library/Extensions/AMDRadeonVADriver2.bundle/Contents/MacOS/AMDRadeonVADriver2"
        base = int(lines[lines.index(image) + 1].split()[1], 16)
        names, _ = symbols(cache.read, base)
        for symbol, suffix in [(PITCH, "Pitch"), (SIZE, "Size"), (AVC, "Avc"), (HEVC, "Hevc")]:
            address = names[symbol]
            end = min(a for a in names.values() if a > address)
            original = cache.read(address, end - address)
            assert hashlib.sha256(original).hexdigest() == HASHES[symbol]
            patched = bytearray(original)
            row = next(r for r in rows if r["label"] == symbol)
            offset = row["address"] - address
            old, new = bytes.fromhex(row["original"]), bytes.fromhex(row["patched"])
            assert original[offset:offset + len(old)] == old and len(old) == len(new)
            patched[offset:offset + len(new)] = new
            for variant, code in [("stock", original), ("fixed", patched)]:
                if suffix in ["Avc", "Hevc"]:
                    # 状态解码前缀没有外部调用；补全其栈恢复，真实位移与分支保持原样。
                    assert code[0x5F:0x61] == bytes.fromhex("8906")
                    code = code[:0x61] + bytes.fromhex("4883c4085b415c415d415e415f5dc3")
                assembly += f".globl _{variant}{suffix}\n_{variant}{suffix}:\n.byte "
                assembly += ",".join(map(str, code)) + "\n"
    finally:
        cache.close()
    source = WORK / "native.s"
    source.write_text(assembly)
    executable = WORK / "test-native"
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tools/fixtures/vcn-encode-native.cpp"), str(source), "-o", str(executable)],
                   check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
