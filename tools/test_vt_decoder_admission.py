"""验证通用 VideoToolbox 补丁；--live 在当前测试进程的私有页执行真实筛选对照。"""
import argparse
from pathlib import Path
import re
import subprocess

import build_vp9_decode_patch as gen
from audit_video_driver import symbols
from metal_cache import CacheReader, array
import vt_decoder_selection as selection

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/vp9/vt-admission'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    data = (ROOT / 'NootedRed/UserVP9DecodeData.hpp').read_text()
    index = int(re.search(r'DecoderAdmissionPatch = (\d+);', data)[1])
    def patch_array(name):
        text = re.search(rf'{name}{index}\[\] = \{{(.*?)\}};', data, re.S)[1]
        return bytes(int(n, 16) for n in re.findall(r'0x([\dA-F]{2})', text))
    original, replacement = patch_array('Original'), patch_array('Patched')
    assert len(original) == len(replacement) == 31
    assembly = '.text\n'
    for label, code in [('nativeConversion', original), ('admittedConversion', replacement)]:
        assembly += (f'.globl _{label}\n_{label}:\npushq %rbp\nmovq %rsp,%rbp\npushq %rbx\n'
                     'subq $56,%rsp\nmovq %rdi,%rbx\nmovl %esi,-0x2f(%rbp)\n.byte '
                     + ','.join(map(str, code))
                     + '\nmovl %edi,%eax\naddq $56,%rsp\npopq %rbx\npopq %rbp\nretq\n')
    (WORK / 'conversions.s').write_text(assembly)
    executable = WORK / 'offline'
    subprocess.run(['xcrun', 'clang++', '-arch', 'x86_64', '-std=c++23', '-O1', '-fsanitize=address,undefined',
                    str(ROOT / 'tools/fixtures/vt-decoder-admission.cpp'), str(WORK / 'conversions.s'),
                    '-o', str(executable)], check=True, timeout=60)
    subprocess.run([str(executable)], check=True, timeout=60)
    if not args.live:
        return
    cache = CacheReader(uuid=gen.DEFAULT_UUID)
    try:
        address, native = selection.prepare(cache)
        patched = selection.patched(native)
        assert native[:0x33F] == patched[:0x33F] and native[0x35E:] == patched[0x35E:]
        assert sum(a != b for a, b in zip(native, patched)) > 0
        lines = (cache.directory / 'dyld_shared_cache_x86_64h.map').read_text().splitlines()
        names, _ = symbols(cache.read, int(lines[lines.index(selection.VT) + 1].split()[1], 16))
        query = names['_VTIsHardwareDecodeSupported']
    finally:
        cache.close()
    header = '#include <cstdint>\n'
    header += f'constexpr uintptr_t QueryAddress={query:#x}ULL, FilterAddress={address:#x}ULL;\n'
    header += array('Native', native).replace('UInt8', 'uint8_t')
    header += array('Patched', patched).replace('UInt8', 'uint8_t')
    (WORK / 'vt-admission-reference.hpp').write_text(header)
    executable = WORK / 'live'
    subprocess.run(['xcrun', 'clang++', '-std=c++23', '-fobjc-arc', '-O1', '-g',
                    '-framework', 'Foundation', '-framework', 'VideoToolbox', '-framework', 'CoreMedia',
                    '-framework', 'CoreVideo', '-framework', 'CoreGraphics', '-I' + str(WORK),
                    '-framework', 'AVFoundation',
                    str(ROOT / 'tools/fixtures/vt-decoder-admission-live.mm'), '-o', str(executable)],
                   check=True, timeout=60)
    clip = WORK / 'short.ivf'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=320x180:r=1', '-frames:v', '3', '-c:v', 'libvpx-vp9',
                    '-deadline', 'realtime', '-cpu-used', '8', '-f', 'ivf', '-y', str(clip)],
                   check=True, timeout=60)
    av1 = WORK / 'short-av1.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=320x180:r=1', '-frames:v', '3', '-c:v', 'libsvtav1',
                    '-preset', '12', '-crf', '40', '-y', str(av1)], check=True, timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run([str(executable), str(clip), str(av1)], check=True, timeout=60)


if __name__ == '__main__':
    main()
