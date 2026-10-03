"""编译实际视频 COW 桥接代码，验证每个写入/保护失败、混合版本拒绝和返回值。"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"


def main():
    stub = WORK / "stubs/IOKit"
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "IOTypes.h").write_text("#pragma once\n#include <cstdint>\n"
                                 "using UInt8=uint8_t;using UInt32=uint32_t;using UInt64=uint64_t;\n")
    source = (ROOT / "NootedRed/UserVideoDecode.cpp").read_text()
    namespace = re.search(r"namespace\s*\{", source)
    closing = re.search(r"\}\s*// namespace", source)
    assert namespace is not None and closing is not None
    body = source[namespace.end():closing.start()]
    init = source[source.index("void UserVideoDecode::init("):]
    template = (ROOT / "tools/fixtures/vcn-user-bridge.cpp.in").read_text()
    harness = WORK / "test-user-bridge.cpp"
    harness.write_text(template.replace("BODY", body).replace("INIT", init))
    executable = WORK / "test-user-bridge"
    subprocess.run(["xcrun", "clang++", "-std=c++23", "-O2", "-I" + str(WORK / "stubs"),
                    "-I" + str(ROOT / "NootedRed"), str(harness), "-o", str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)


if __name__ == "__main__":
    main()
