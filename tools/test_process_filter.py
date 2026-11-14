"""验证共享缓存补丁的进程过滤：仅跳过精确白名单中的基础系统命令。

主机编译 NootedRed/UserProcessFilter.hpp，对合成 Mach-O 边界情况与本机真实可执行文件分类。
--live 额外统计当前运行进程（需要 ps），并要求 WindowServer 与 /Applications 应用全部保持补丁。
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/process-filter'

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include <UserProcessFilter.hpp>
using namespace UserProcessFilter;

static UInt32 readFile(void* ctx, UInt64 offset, void* out, UInt32 size) {
    auto* f = static_cast<FILE*>(ctx);
    if (fseeko(f, static_cast<off_t>(offset), SEEK_SET) != 0) { return 0; }
    return static_cast<UInt32>(fread(out, 1, size, f));
}

struct Mem { std::vector<UInt8> bytes; };
static UInt32 readMem(void* ctx, UInt64 offset, void* out, UInt32 size) {
    auto* m = static_cast<Mem*>(ctx);
    if (offset >= m->bytes.size()) { return 0; }
    const auto n = static_cast<UInt32>(std::min<UInt64>(size, m->bytes.size() - offset));
    memcpy(out, m->bytes.data() + offset, n);
    return n;
}

static void put32(std::vector<UInt8>& b, UInt32 v) { for (int i = 0; i < 4; ++i) b.push_back((v >> (8 * i)) & 0xFF); }
static void putBig32(std::vector<UInt8>& b, UInt32 v) { for (int i = 3; i >= 0; --i) b.push_back((v >> (8 * i)) & 0xFF); }

static Mem machO(const std::vector<const char*>& dylibs, UInt32 cpu = 0x01000007, UInt32 type = 2, bool fat = false) {
    std::vector<UInt8> cmds;
    for (auto name : dylibs) {
        UInt32 size = (24 + static_cast<UInt32>(strlen(name)) + 1 + 7) & ~7U;
        put32(cmds, 0xC); put32(cmds, size); put32(cmds, 24); put32(cmds, 0); put32(cmds, 0); put32(cmds, 0);
        for (size_t i = 0; i < size - 24; ++i) cmds.push_back(i < strlen(name) ? name[i] : 0);
    }
    std::vector<UInt8> mh;
    put32(mh, 0xFEEDFACF); put32(mh, cpu); put32(mh, 3); put32(mh, type);
    put32(mh, static_cast<UInt32>(dylibs.size())); put32(mh, static_cast<UInt32>(cmds.size())); put32(mh, 0); put32(mh, 0);
    mh.insert(mh.end(), cmds.begin(), cmds.end());
    if (!fat) { return {mh}; }
    std::vector<UInt8> f;
    putBig32(f, 0xCAFEBABE); putBig32(f, 2);
    putBig32(f, 0x0100000C); putBig32(f, 0); putBig32(f, 0x4000); putBig32(f, 16); putBig32(f, 14);
    putBig32(f, 0x01000007); putBig32(f, 3); putBig32(f, 0x8000); putBig32(f, static_cast<UInt32>(mh.size())); putBig32(f, 14);
    f.resize(0x8000, 0);
    f.insert(f.end(), mh.begin(), mh.end());
    return {f};
}

static Verdict run(const char* path, Mem m) {
    static UInt8 header[HeaderBytes], commands[MaxCommandBytes];
    return classify(path, static_cast<UInt32>(strlen(path)), readMem, &m, header, commands);
}

static void synthetic() {
    const char* sys = "/usr/lib/libSystem.B.dylib";
    assert(run("/usr/bin/true", machO({sys})) == Verdict::Skip);
    assert(run("/usr/bin/true", machO({sys}, 0x01000007, 2, true)) == Verdict::Skip);
    for(auto path:{"/bin/cp","/bin/rm","/bin/mkdir","/usr/bin/awk","/usr/bin/sed",
                   "/usr/bin/sort","/usr/bin/tar","/usr/bin/xargs","/usr/bin/wc"}) {
        assert(run(path,machO({sys}))==Verdict::Skip);
    }
    assert(run("/usr/bin/grep-extra",machO({sys}))==Verdict::Patch);
    assert(run("/usr/bin/sed/../osascript",machO({sys}))==Verdict::Patch);
    // 路径不在范围内：保持原补丁行为。
    assert(run("/Applications/App.app/Contents/MacOS/App", machO({sys})) == Verdict::Patch);
    assert(run("/usr/local/bin/tool", machO({sys})) == Verdict::Patch);
    assert(run("/usr/bin/../../Applications/x", machO({sys})) == Verdict::Patch);
    // 直接依赖相同，也不能推断未知程序、解释器或工具链不会 dlopen GPU 框架。
    for (auto path : {"/usr/bin/tool", "/bin/zsh", "/bin/bash", "/usr/bin/osascript",
                      "/Library/Developer/CommandLineTools/usr/bin/dynamic-gpu-tool",
                      "/Applications/Xcode.app/Contents/Developer/Toolchains/custom"}) {
        assert(run(path, machO({sys})) == Verdict::Patch);
    }
    // GPU / 媒体 / 私有 / rpath 依赖：必须补丁。
    for (auto dep : {"/usr/lib/libUnknown.dylib", "/usr/lib/libxo.dylib",
                     "/System/Library/Frameworks/Metal.framework/Versions/A/Metal",
                     "/System/Library/Frameworks/AppKit.framework/Versions/C/AppKit",
                     "/System/Library/Frameworks/CoreImage.framework/Versions/A/CoreImage",
                     "/System/Library/Frameworks/VideoToolbox.framework/Versions/A/VideoToolbox",
                     "/System/Library/Frameworks/ImageIO.framework/Versions/A/ImageIO",
                     "/System/Library/Frameworks/QuartzCore.framework/Versions/A/QuartzCore",
                     "/System/Library/Frameworks/OpenGL.framework/Versions/A/OpenGL",
                     "/System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/SkyLight",
                     "/System/Library/PrivateFrameworks/Anything.framework/Versions/A/Anything",
                     "/usr/lib/swift/libswiftMetal.dylib", "/usr/lib/swift/libswiftCoreImage.dylib",
                     "/usr/lib/swift/libswiftAppKit.dylib", "@rpath/libFoo.dylib",
                     "@executable_path/../lib/libbar.dylib", "/opt/homebrew/lib/libavcodec.dylib",
                     "/System/Library/Frameworks/FoundationX.framework/FoundationX",
                     "/System/Library/Frameworks/Foundation"}) {
        assert(run("/usr/bin/true", machO({sys, dep})) == Verdict::PatchDependency);
    }
    // 格式问题：保守补丁。
    assert(run("/usr/bin/true", machO({})) == Verdict::PatchFormat);
    assert(run("/usr/bin/true", machO({sys}, 0x0100000C)) == Verdict::PatchFormat);
    assert(run("/usr/bin/true", machO({sys}, 0x01000007, 6)) == Verdict::PatchFormat);
    auto truncated = machO({sys}); truncated.bytes.resize(40);
    assert(run("/usr/bin/true", truncated) == Verdict::PatchFormat);
    assert(run("/usr/bin/true", Mem{}) == Verdict::PatchFormat);
    auto badName = machO({sys}); badName.bytes[32 + 8] = 200;
    assert(run("/usr/bin/true", badName) == Verdict::PatchFormat);
    auto noNul = machO({sys}); for (size_t i = 32 + 24; i < noNul.bytes.size(); ++i) noNul.bytes[i] = 'A';
    assert(run("/usr/bin/true", noNul) == Verdict::PatchFormat);
    puts("PASS synthetic Mach-O edge cases");
}

int main(int argc, char** argv) {
    synthetic();
    static UInt8 header[HeaderBytes], commands[MaxCommandBytes];
    unsigned counts[4]{};
    for (int i = 1; i < argc; ++i) {
        FILE* f = fopen(argv[i], "rb");
        Verdict v = Verdict::PatchFormat;
        if (f) {
            v = classify(argv[i], static_cast<UInt32>(strlen(argv[i])), readFile, f, header, commands);
            fclose(f);
        }
        ++counts[static_cast<int>(v)];
        printf("%d %s\n", static_cast<int>(v), argv[i]);
    }
    fprintf(stderr, "patch=%u skip=%u format=%u dependency=%u\n", counts[0], counts[1], counts[2], counts[3]);
}
'''


def build():
    stub = WORK / 'stubs/IOKit'
    stub.mkdir(parents=True, exist_ok=True)
    (stub / 'IOTypes.h').write_text(
        '#pragma once\n#include <cstdint>\n'
        'using UInt8=uint8_t;using UInt16=uint16_t;using UInt32=uint32_t;using UInt64=uint64_t;\n')
    source, executable = WORK / 'harness.cpp', WORK / 'harness'
    source.write_text(HARNESS)
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-O1', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I' + str(WORK / 'stubs'), '-I' + str(ROOT / 'NootedRed'),
                    str(source), '-o', str(executable)], check=True, timeout=120)
    return executable


def classify(executable, paths):
    out = subprocess.run([str(executable), *paths], capture_output=True, text=True, timeout=300, check=True)
    verdicts = {}
    for line in out.stdout.splitlines():
        if line.startswith('PASS'):
            print(line)
            continue
        code, path = line.split(' ', 1)
        verdicts[path] = int(code)
    return verdicts, out.stderr.strip()


def system_tools():
    paths = []
    for directory in ('/bin', '/sbin', '/usr/bin', '/usr/sbin', '/usr/libexec'):
        for entry in sorted(Path(directory).iterdir()):
            try:
                if entry.is_file() and not entry.is_symlink():
                    paths.append(str(entry))
            except OSError:
                continue
    return paths


def running():
    out = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip().startswith('/')]


def main():
    executable = build()
    names = {0: 'patch(path)', 1: 'SKIP', 2: 'patch(format)', 3: 'patch(dependency)'}
    verdicts, summary = classify(executable, system_tools())
    print('system tools:', summary)
    for required in ('/bin/ls', '/bin/cat', '/usr/bin/grep', '/usr/bin/env'):
        if required in verdicts:
            assert verdicts[required] == 1, f'{required} should be skipped: {names[verdicts[required]]}'
    for required in ('/usr/sbin/screencapture', '/usr/bin/sips', '/usr/bin/qlmanage', '/usr/bin/avconvert'):
        if required in verdicts:
            assert verdicts[required] != 1, f'{required} uses graphics frameworks and must stay patched'
    if '--live' in sys.argv:
        procs = sorted(set(running()))
        live, summary = classify(executable, procs)
        counts = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True).stdout.splitlines()
        skipped = sum(1 for c in counts if live.get(c.strip()) == 1)
        print(f'running executables (unique): {summary}; running processes skipped: {skipped}/{len(counts)}')
        for path, verdict in live.items():
            if path.startswith('/Applications/') and '.app/Contents/MacOS/' in path:
                assert verdict != 1, f'app must stay patched: {path}'
            if path.endswith('/WindowServer'):
                assert verdict != 1, 'WindowServer must stay patched'
        if '--verbose' in sys.argv:
            for path, verdict in sorted(live.items()):
                print(f'  {names[verdict]:18} {path}')
    print('PASS process filter: only explicitly allowed system tools are skipped')


if __name__ == '__main__':
    main()
