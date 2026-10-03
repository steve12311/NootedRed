"""从核实的本机驱动生成 VCN 桥接调用补丁，不修改系统驱动。"""
from pathlib import Path
import hashlib
import json
import re
import struct
import subprocess

from audit_video_driver import disk_image

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"
DRIVER = Path("/System/Library/Extensions/AMDRadeonX6000.kext/Contents/MacOS/AMDRadeonX6000")


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    data, read = disk_image(DRIVER)
    result = subprocess.run(["xcrun", "llvm-objdump", "-d", str(DRIVER)], capture_output=True,
                            text=True, check=True, timeout=60)
    lines = result.stdout.splitlines()
    symbol, base = "", 0
    records = []
    for i, line in enumerate(lines):
        match = re.match(r"^([0-9a-f]+) <([^>]+)>:", line)
        if match:
            symbol, base = match[2], int(match[1], 16)
        instruction = re.match(
            r"^\s*([0-9a-f]+): ((?:[0-9a-f]{2} )+)\s*callq\s+\*0x(2a0|2b0|3b0|3c0|230|3e0|3e8)\(%rax\)", line)
        if not instruction:
            continue
        address, slot = int(instruction[1], 16), int(instruction[3], 16)
        accepted = (symbol.startswith("__ZN27AMDRadeonX6000_AMDHWChannel")
                    and slot in [0x2A0, 0x3B0, 0x3C0, 0x230])
        accepted |= "AMDVCN2HWEngine6isIdle" in symbol and slot == 0x2A0
        accepted |= "AMDNavi10VideoContext21setSuspendResumeState" in symbol and slot == 0x2B0
        idle = symbol in ["__ZN27AMDRadeonX6000_AMDHWChannel11waitForIdleEj",
                          "__ZN26AMDRadeonX6000_AMDHWEngine11waitForIdleEj"] and slot in [0x3E0, 0x3E8]
        accepted |= idle
        if not accepted:
            continue
        original = read(address, 6)
        assert original == b"\xff\x90" + struct.pack("<I", slot)
        previous = "\n".join(lines[i - 5:i])
        # 仅收录已核实从 channel/interface 字段加载的调用；排除 engine/self 同偏移虚调用。
        if not re.search(r"movq\s+\(%rdi\), %rax", previous):
            raise ValueError(f"缺少硬件虚表加载证据：{symbol}+{address - base:#x}")
        if idle:
            field = "20" if "AMDHWChannel" in symbol else "18"
            if not re.search(rf"movq\s+0x{field}\(%r(?:di|bx)\), %rdi", previous):
                raise ValueError(f"缺少等待路径硬件接收者证据：{symbol}")
        records.append({"symbol": symbol, "offset": address - base, "original": original.hex(),
                        "newSlot": {0x2A0: 0x298, 0x2B0: 0x2A8, 0x3B0: 0x3B8,
                                    0x3C0: 0x3C8, 0x230: 0, 0x3E0: 0x3E8, 0x3E8: 0x3F0}[slot]})
    if len([r for r in records if "11waitForIdleEj" in r["symbol"]]) != 4:
        raise ValueError("通道与引擎的 GFXOFF 配对补丁必须各有两处")
    if not records or len(records) > 32:
        raise ValueError("VCN 补丁数量异常")
    header = "// 由 tools/build_vcn_kernel_patch.py 生成，不手工编辑。\n#pragma once\n#include <IOKit/IOTypes.h>\n"
    header += "namespace VCNKernelData {\n"
    header += "struct Patch { const char* symbol; UInt32 offset; UInt8 original[6]; UInt32 newSlot; };\n"
    header += "inline constexpr Patch Patches[] = {\n"
    for record in records:
        original = ", ".join(f"0x{v:02X}" for v in bytes.fromhex(record["original"]))
        header += f'    {{"{record["symbol"]}", 0x{record["offset"]:X}, {{{original}}}, 0x{record["newSlot"]:X}}},\n'
    header += "};\n}\n"
    (ROOT / "NootedRed/VCNKernelData.hpp").write_text(header)
    (WORK / "kernel-patches.json").write_text(json.dumps({"driverSHA256": hashlib.sha256(data).hexdigest(),
                                                        "records": records}, indent=2) + "\n")
    print("Generated kernel calls:", len(records))


if __name__ == "__main__":
    main()
