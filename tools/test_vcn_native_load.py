"""编译实际启动 hook，验证加载先于原生 start、延迟加载拒绝和返回值保留。"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"


def function(source, prefix):
    start = source.index(prefix)
    line = source[start:source.index("\n", start)]
    if "{" in line and line.rstrip().endswith("}"):
        return line
    indentation = start - source.rfind("\n", 0, start) - 1
    end = re.search(r"\n" + " " * indentation + r"\}(?=\n|$)", source[start:])
    assert end is not None, prefix
    return source[start:start + end.end()]


def main():
    source = (ROOT / "NootedRed/VCN.cpp").read_text()
    assert re.search(r'\{"__ZN37AMDRadeonX5000_AMDGraphicsAccelerator5startEP9IOService",\s*acceleratorStart,', source)
    assert "OSKextLoadKextWithIdentifier" not in function(source, "void VCN::init()")
    functions = "\n".join(function(source, p) for p in ["bool supported()", "bool acceleratorStart(", "bool VCN::ready()"])
    fixture = (ROOT / "tools/fixtures/vcn-native-load.cpp.in").read_text()
    WORK.mkdir(parents=True, exist_ok=True)
    harness = WORK / "test-native-load.cpp"
    stubs = WORK / "stubs/IOKit"
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / "IOTypes.h").write_text("#pragma once\n#include <cstdint>\nusing UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n")
    util = WORK / "stubs/Headers"
    util.mkdir(parents=True, exist_ok=True)
    (util / "kern_util.hpp").write_text('#pragma once\n#include <IOKit/IOTypes.h>\n')
    capabilities = '#include <VCNCapabilities.hpp>'
    harness.write_text(fixture.replace("FUNCTIONS", functions).replace("CAPABILITIES", capabilities))
    executable = WORK / "test-native-load"
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-Wall", "-Wextra", "-Werror",
                    "-I"+str(WORK / "stubs"), "-I"+str(ROOT / "NootedRed"), str(harness),
                    "-o", str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == "__main__":
    main()
