"""离线执行原生等待代码：验证跨驱动 GFXOFF 配对及超时出口，不访问 GPU。"""
from pathlib import Path
import argparse
import json
import re
import struct
import subprocess

from audit_video_driver import disk_image, vtable

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"


def native_image(version):
    path = Path(f"/System/Library/Extensions/AMDRadeonX{version}.kext/Contents/MacOS/AMDRadeonX{version}")
    _, read = disk_image(path)
    names = {}
    result = subprocess.run(["nm", "-n", str(path)], capture_output=True, text=True, check=True, timeout=60)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3:
            names[fields[2]] = int(fields[0], 16)
    disassembly = subprocess.run(["xcrun", "llvm-objdump", "-d", str(path)], capture_output=True,
                                 text=True, check=True, timeout=60).stdout
    functions = {}
    for block in re.split(r"(?=^[0-9a-f]+ <)", disassembly, flags=re.M):
        match = re.match(r"([0-9a-f]+) <([^>]+)>:", block)
        if match:
            functions[match[2]] = (int(match[1], 16), block)
    return read, names, functions


def emit(version, kind, patched, functions, records):
    symbol = f"__ZN{27 if kind == 'Channel' else 26}AMDRadeonX{version}_AMDHW{kind}11waitForIdleEj"
    base, block = functions[symbol]
    label = f"wait{version}{kind}{'Patched' if patched else 'Stock'}"
    selected = [r for r in records if r["symbol"] == symbol] if patched else []
    applied = []
    lines = [f".globl _{label}", f"_{label}:"]
    for line in block.splitlines()[1:]:
        match = re.match(r"\s*([0-9a-f]+): ((?:[0-9a-f]{2} )+)\s*(.*)", line)
        if not match:
            continue
        address, raw, instruction = int(match[1], 16), bytes.fromhex(match[2]), match[3]
        for record in selected:
            if address - base == record["offset"]:
                assert raw == bytes.fromhex(record["original"])
                raw = raw[:2] + struct.pack("<I", record["newSlot"])
                applied.append(record)
        # 保留全部原生分支和字节长度，仅重定位外部计时、追踪依赖到离线 mock。
        if re.match(r"callq\s+0x", instruction):
            target = "mockPollWait" if "IAMDHWInterface8pollWait" in instruction else "mockTrace"
            assert len(raw) == 5 and raw[0] == 0xE8
            lines.append(f"callq _{target}")
        elif "(%rip)" in instruction:
            assert len(raw) == 7 and instruction.startswith("movq")
            lines.append(re.sub(r"0x[0-9a-f]+\(%rip\)", "_mockTracePointer(%rip)",
                                instruction.split("##")[0].strip()))
        else:
            lines.append(".byte " + ",".join(hex(v) for v in raw))
    assert len(applied) == len(selected)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patches", type=Path, default=WORK / "kernel-patches.json")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    records = json.loads(args.patches.read_text())["records"]
    assembly = [".text"]
    for version in ["5000", "6000"]:
        read, names, functions = native_image(version)
        classname = "AMDVega10Hardware" if version == "5000" else "AMDNavi10Hardware"
        slots = vtable(read, names, classname)["slots"]
        # 从本机虚表证明函数身份，避免测试自己复制的槽号表。
        for suffix, offset in [("13disableGfxOffEv", 0x3E8 if version == "5000" else 0x3E0),
                               ("12enableGfxOffEv", 0x3F0 if version == "5000" else 0x3E8)]:
            assert slots[offset // 8]["target"] == names[f"__ZN28AMDRadeonX{version}_AMDRTHardware{suffix}"]
        for kind in ["Channel", "Engine"]:
            assembly.append(emit(version, kind, False, functions, records))
            if version == "6000":
                assembly.append(emit(version, kind, True, functions, records))
    asm = WORK / "native-idle.s"
    asm.write_text("\n".join(assembly) + "\n")
    executable = WORK / "test-idle-calls"
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
                    str(ROOT / "tools/fixtures/vcn-idle-calls.cpp"), str(asm), "-o", str(executable)],
                   check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)
    print("原生通道/引擎：成功、轮询完成、超时均保留返回值且正确配对 GFXOFF；未修补版本缺陷已复现。")


if __name__ == "__main__":
    main()
