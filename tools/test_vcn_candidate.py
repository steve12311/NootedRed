"""执行候选识别函数与 GFX9 工厂分支，确认 PCI 特例及错误出口，不提交 GPU 工作。"""
import ast
from pathlib import Path
import re
import subprocess
import test_video_driver_identify
from vcn_capabilities import devices

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"


def string_fixture(name, marker):
    tree = ast.parse((ROOT / "tools" / name).read_text())
    return next(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                and isinstance(n.value, str) and marker in n.value)


def main():
    test_video_driver_identify.main()
    stock_source = (test_video_driver_identify.WORK / "native-va-identify.s").read_text()
    stock = WORK / "stock-identify.s"
    stock.write_text(stock_source.replace("_nativeIdentify", "_stockIdentify"))
    assert devices() == [(0x15E7, 5), (0x1636, 5), (0x1638, 5), (0x164C, 5)]
    for device, _ in devices():
        per_device = WORK / f"identify-{device:04X}.s"
        source = per_device.read_text().replace("_payload", "_nativeIdentify")
        for label, replacement in [("24", "leaq _fakeHardwareInfo(%rip), %rax"),
                                   ("48", "callq _readHwInfo"),
                                   ("5a", "movzwl _fakeHardwareInfo+4(%rip), %eax")]:
            expression = rf"L{label}:\n\.byte [^\n]+\n\.long [^\n]+"
            source, count = re.subn(expression, f"L{label}:\n{replacement}", source)
            assert count == 1
        fixture = string_fixture("test_video_driver_identify.py", "uint32_t nativeIdentify")
        fixture = fixture.replace("uint32_t nativeIdentify(uint32_t connection, uint32_t type);",
                                  "uint32_t nativeIdentify(uint32_t connection, uint32_t type);\n"
                                  "uint32_t stockIdentify(uint32_t connection, uint32_t type);")
        fixture = fixture.replace("counts[result]++;", "counts[result]++;\n"
                                  "        auto stock = stockIdentify(0x1234, 1);\n"
                                  f"        assert(result == (id == {device} ? 5 : stock));")
        fixture = fixture.replace("if (id == 0x1638 || id == 0x1636 || id == 0x15dd || id == 0x15d8) { assert(result == 11); }",
                                  f"if (id == {device}) {{ assert(result == 5); }}")
        fixture = fixture.replace("Cezanne/Renoir/Raven return unsupported enum 11",
                                  f"selected PCI {device:#06x} returns 5; other PCI classifications preserved")
        asm, cpp = WORK / "candidate-identify.s", WORK / "candidate-identify.cpp"
        asm.write_text(source)
        cpp.write_text(fixture)
        executable = WORK / "test-candidate-identify"
        subprocess.run(["xcrun", "clang++", "-std=c++23", "-O2", str(cpp), str(asm), str(stock), "-o", str(executable)],
                       check=True, timeout=60)
        subprocess.run([str(executable)], check=True, timeout=60)

if __name__ == "__main__":
    main()
