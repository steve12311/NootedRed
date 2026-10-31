"""生成提交守卫数据：核验 AMD Metal 驱动方法入口，汇编守卫并嵌入头文件。"""
from pathlib import Path
import argparse
import hashlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/submit-guard"
OUT = ROOT / "NootedRed/UserSubmitGuardData.hpp"
sys.path.insert(0, str(ROOT / "tools"))

from metal_cache import CacheReader, macho  # noqa: E402

METHOD = '-[GFX9AMD_MtlCmdQueue submitCommandBuffers:count:]'
METHOD_SHA256 = '5dafebcef79f820373dacf9998519ca35cdb87987805c150e15ee9eb0bb0886d'
METHOD_ORIGINAL = bytes.fromhex("554889e54883ec10488d45f0488938")
METHOD_PATCH_SIZE = len(METHOD_ORIGINAL)
PLACEHOLDER = bytes.fromhex("49bb8877665544332211")  # movabs $0x1122334455667788,%r11


def array(name: str, data: bytes) -> str:
    lines = [f"inline constexpr UInt8 {name}[] = {{"]
    for i in range(0, len(data), 12):
        lines.append("    " + ", ".join(f"0x{b:02X}" for b in data[i:i + 12]) + ",")
    lines.append("};")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    cache = CacheReader()
    try:
        address, code = cache.function(METHOD)
        if hashlib.sha256(code).hexdigest() != METHOD_SHA256 or code[:METHOD_PATCH_SIZE] != METHOD_ORIGINAL:
            raise SystemExit("AMD Metal 提交方法未核实，拒绝生成")
        method_offset = address - cache.base
    finally:
        cache.close()

    WORK.mkdir(parents=True, exist_ok=True)
    obj = WORK / "submit_guard.o"
    subprocess.run(["as", "-arch", "x86_64", str(ROOT / "tools/submit_guard.s"), "-o", str(obj)],
                   check=True, timeout=60)
    data = obj.read_bytes()
    _, sections, _ = macho(lambda a, n: data[a:a + n], 0)
    text = next(s for s in sections if s[0] == "__text")
    payload = data[text[3]:text[3] + text[2]]
    if len(payload) >= 4096:
        raise SystemExit("守卫超过一页")
    if payload.count(PLACEHOLDER) != 1:
        raise SystemExit("守卫占位符数量不为 1")
    jump_offset = payload.index(PLACEHOLDER) + 2

    header = "// 由 tools/build_submit_guard.py 生成，不手工编辑。\n#pragma once\n#include <IOKit/IOTypes.h>\n"
    header += "namespace UserSubmitGuardData {\n"
    header += array("MethodOriginal", METHOD_ORIGINAL)
    header += f"inline constexpr UInt32 MethodPatchSize = {METHOD_PATCH_SIZE};\n"
    header += f"inline constexpr UInt32 PayloadJumpOffset = {jump_offset};\n"
    header += array("Payload", payload)
    header += "} // namespace UserSubmitGuardData\n"
    if args.check:
        if OUT.read_text() != header:
            raise SystemExit("提交守卫生成数据未同步")
    else:
        OUT.write_text(header)
    print(f"submit guard: method 0x{method_offset:X} payload {len(payload)} bytes "
          f"jump offset {jump_offset} -> {OUT}")


if __name__ == "__main__":
    main()
