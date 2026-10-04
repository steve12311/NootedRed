"""从核实的本机驱动生成 VCN 桥接调用补丁，不修改系统驱动。"""
from pathlib import Path
import hashlib
import json
import re
import struct
import subprocess

from audit_video_driver import disk_image, vtable
from metal_cache import array

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"
DRIVER = Path("/System/Library/Extensions/AMDRadeonX6000.kext/Contents/MacOS/AMDRadeonX6000")
GETTERS = {
    '5000': ['554889e58a870e03000024015dc3', '554889e5488b87400602005dc3', '554889e58b87500502005dc3'],
    '6000': ['554889e58a870e03000024015dc3', '554889e5488b87480602005dc3', '554889e58b87480502005dc3'],
}


def abi_guards(version):
    path = Path(f"/System/Library/Extensions/AMDRadeonX{version}.kext/Contents/MacOS/AMDRadeonX{version}")
    _, read = disk_image(path)
    names = {}
    for line in subprocess.run(['nm', '-n', str(path)], check=True, capture_output=True, text=True, timeout=60).stdout.splitlines():
        fields = line.split()
        if len(fields) == 3:
            names[fields[2]] = int(fields[0], 16)
    disassembly = subprocess.run(['xcrun', 'llvm-objdump', '-d', str(path)], check=True,
                                capture_output=True, text=True, timeout=60).stdout
    instructions = {}
    for line in disassembly.splitlines():
        match = re.match(r'\s*([0-9a-f]+): ((?:[0-9a-f]{2}[ \t]+)+)(.*)', line)
        if match:
            instructions[int(match[1], 16)] = (bytes.fromhex(match[2]), match[3].strip())
    old = version == '5000'
    hardware = vtable(read, names, 'AMDVega10Hardware' if old else 'AMDNavi10Hardware')
    offsets = [0x298, 0x2A8, 0x3B8, 0x3C8, 0x3E8, 0x3F0] if old else [0x2A0, 0x2B0, 0x3B0, 0x3C0, 0x3E0, 0x3E8]
    targets = [f'__ZN26AMDRadeonX{version}_AMDHardware13isDeviceValidEv', None, None, None,
               f'__ZN28AMDRadeonX{version}_AMDRTHardware13disableGfxOffEv',
               f'__ZN28AMDRadeonX{version}_AMDRTHardware12enableGfxOffEv']
    result = []
    for offset, symbol in zip(offsets, targets):
        address = hardware['slots'][offset // 8]['target']
        code = b''
        if symbol:
            if names[symbol] != address:
                raise ValueError('硬件虚表槽身份未核实')
        else:
            while len(code) < 32:
                raw, instruction = instructions[address + len(code)]
                if instruction.split()[0] not in ['pushq', 'movq', 'movb', 'movl', 'andb', 'popq', 'retq'] or '%rip' in instruction:
                    raise ValueError('getter 出现未核实的调用、分支或相对引用')
                code += raw
                if instruction.startswith('retq'):
                    break
            if not code.endswith(b'\xc3') or len(code) > 32 or read(address, len(code)) != code:
                raise ValueError('getter 内容不完整')
            if code.hex() != GETTERS[version][len([entry for entry in result if entry['code']])]:
                raise ValueError('getter 字段布局变化，需要重新核实')
        result.append({'slot': offset, 'target': symbol, 'code': code.hex()})
    return hardware['symbol'], result


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
    header += "};\n"
    header += 'struct SlotGuard { UInt32 slot; const char* target; const UInt8* code; UInt32 size; };\n'
    guards = {}
    for version in ['5000', '6000']:
        table, entries = abi_guards(version)
        guards[version] = {'table': table, 'entries': entries}
        header += f'inline constexpr char Table{version}[] = "{table}";\n'
        for i, entry in enumerate(entries):
            if entry['code']:
                header += array(f'Getter{version}_{i}', bytes.fromhex(entry['code']))
        header += f'inline constexpr SlotGuard Guards{version}[] = {{\n'
        for i, entry in enumerate(entries):
            target = f'"{entry["target"]}"' if entry['target'] else 'nullptr'
            code = f'Getter{version}_{i}' if entry['code'] else 'nullptr'
            size = len(bytes.fromhex(entry['code']))
            header += f'    {{0x{entry["slot"]:X}, {target}, {code}, {size}}},\n'
        header += '};\n'
    header += '}\n'
    (ROOT / "NootedRed/VCNKernelData.hpp").write_text(header)
    (WORK / "kernel-patches.json").write_text(json.dumps({"driverSHA256": hashlib.sha256(data).hexdigest(),
                                                        "records": records, "abiGuards": guards}, indent=2) + "\n")
    print("Generated kernel calls:", len(records))


if __name__ == "__main__":
    main()
