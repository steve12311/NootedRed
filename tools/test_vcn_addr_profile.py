"""执行原生/候选地址库转换，比较所有受检 family/revision 及随机设置位。"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"


def main():
    records = json.loads((WORK / "user-patches.json").read_text())["records"]
    record = next(r for r in records if "HwlConvertChipFamily" in r["label"])
    assembly = ".text\n.globl _stockAddr\n_stockAddr:\n.byte "
    assembly += ",".join(hex(v) for v in bytes.fromhex(record["original"]))
    assembly += "\n.globl _candidateAddr\n_candidateAddr:\n.byte "
    assembly += ",".join(hex(v) for v in bytes.fromhex(record["patched"])) + "\n"
    asm, cpp, executable = WORK / "test-addr.s", WORK / "test-addr.cpp", WORK / "test-addr"
    asm.write_text(assembly)
    cpp.write_text((ROOT / "tools/fixtures/vcn-addr-profile.cpp.in").read_text())
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-O2", str(cpp), str(asm), "-o", str(executable)],
                   check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == "__main__":
    main()
