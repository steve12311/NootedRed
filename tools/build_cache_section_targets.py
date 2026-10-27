"""核验完整指令后生成节内目标身份，允许共享缓存独立重排各 Mach-O 节。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from cache_resolver_data import ResolverData
from metal_cache import CacheReader, array, macho

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/cache-section-targets'


def byte_array(source, name):
    match = re.search(r'\b' + name + r'\[\] = \{(.*?)\};', source, re.S)
    if not match:
        raise ValueError(f'缺少参考数组：{name}')
    return bytes(int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})(?![\dA-Fa-f])', match[1]))


def metadata(source):
    block = re.search(r'ResolverImages\[\] = \{(.*?)\};', source, re.S)[1]
    images = re.findall(r'\{"([^"]+)", sizeof\("[^"]+"\)\}', block)
    block = re.search(r'ResolverFunctions\[\] = \{(.*?)\};', source, re.S)[1]
    functions = []
    for image, rva, index in re.findall(r'\{(\d+), 0x([0-9A-F]+)ULL, ResolverOriginal(\d+),', block):
        key = f'ResolverOriginalRefs{index}'
        match = re.search(key + r'\[\] = \{(.*?)\};', source, re.S)
        refs = [(int(o), int(i), int(r, 16), int(k)) for o, i, r, k in
                re.findall(r'\{(\d+), (\d+), 0x([0-9A-F]+)ULL, (\d+)\}', match[1])] if match else []
        functions.append((int(image), int(rva, 16), byte_array(source, f'ResolverOriginal{index}'), refs))
    return images, functions


def sections(cache, image):
    commands = cache.read(image + 32, struct.unpack_from('<I', cache.read(image, 32), 20)[0])
    cursor = 0
    result = []
    while cursor < len(commands):
        kind, length = struct.unpack_from('<II', commands, cursor)
        if kind == 0x19:
            segment = commands[cursor + 8:cursor + 24].rstrip(b'\0').decode()
            for index in range(struct.unpack_from('<I', commands, cursor + 64)[0]):
                start = cursor + 72 + index * 80
                section = commands[start:start + 16].rstrip(b'\0').decode()
                address, size = struct.unpack_from('<QQ', commands, start + 32)
                result.append((segment, section, address, size, cursor + 32, index))
        cursor += length
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path('/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    # UUID 只记录来源；兼容判定使用完整指令及重定位字段，而不是系统小版本白名单。
    with (args.cache / 'dyld_shared_cache_x86_64h').open('rb') as handle:
        handle.seek(88)
        uuid = handle.read(16).hex()
    cache = CacheReader(args.cache, uuid)
    resolver = ResolverData(cache, WORK)
    bases = {path: next(start for name, start, _ in ranges if name == '__TEXT')
             for path, ranges in resolver.catalog}
    targets, layouts, evidence = {}, {}, []

    def add_target(path, reference_rva, actual):
        image = bases[path]
        matches = [s for s in sections(cache, image) if s[2] <= actual and actual + 4 <= s[2] + s[3]]
        if len(matches) != 1:
            raise ValueError(f'引用不属于唯一完整节：{path} {actual:#x}')
        segment, section, start, _, hint, index = matches[0]
        if segment == "__TEXT":
            return
        value = (segment, section, actual - start, hint, index)
        key = path, reference_rva
        if key in targets and targets[key] != value:
            raise ValueError('同一参考目标出现不同身份')
        # 只接受整节移动；节内目标重排需要新的语义证据，不能默默放宽。
        layout = path, segment, section
        old_start = reference_rva - (actual - start)
        if layout in layouts and layouts[layout] != old_start:
            raise ValueError(f'节内布局变化，拒绝生成：{layout}')
        layouts[layout] = old_start
        targets[key] = value

    try:
        for filename in ['UserSurfaceSyncData.hpp', 'UserVideoDecodeData.hpp']:
            source = (ROOT / 'NootedRed' / filename).read_text()
            if filename == 'UserSurfaceSyncData.hpp':
                source = source.split('namespace ScratchResolverData {', 1)[1]
            images, functions = metadata(source)
            for image, rva, original, refs in functions:
                address = bases[images[image]] + rva
                native = cache.read(address, len(original))
                ignored = {offset + k for offset, _, _, _ in refs for k in range(4)}
                if any(a != b for i, (a, b) in enumerate(zip(original, native)) if i not in ignored):
                    raise ValueError(f'真实指令变化，拒绝生成：{filename} {rva:#x}')
                observed = resolver.references(native, address, f'{filename}-{rva:x}')
                if [x[0] for x in observed] != [x[0] for x in refs]:
                    raise ValueError('外部引用指令边界变化')
                for (offset, owner, old_rva, indirect), (_, current_owner, current_rva, current_kind) in zip(refs, observed):
                    path, base = resolver.images[current_owner]
                    if path != images[owner] or current_kind != indirect:
                        raise ValueError('外部引用类型或目标映像变化')
                    add_target(path, old_rva, base + current_rva)
                evidence.append({'source': filename, 'rva': rva, 'size': len(native),
                                 'sha256': hashlib.sha256(native).hexdigest()})

        source = (ROOT / 'NootedRed/UserSurfaceSyncData.hpp').read_text()
        ctor_name = '-[GFX9_MtlRenderPipelineState initWithDevice:pipelineStateDescriptor:vertexVariant:fragmentVariant:]'
        address, native = cache.function(ctor_name)
        original = byte_array(source, 'BlendConstructor')
        refs = resolver.references(native, address, 'constructor')
        ignored = {offset + k for offset, _, _, _ in refs for k in range(4)}
        if len(native) != len(original) or any(a != b for i, (a, b) in enumerate(zip(original, native)) if i not in ignored):
            raise ValueError('构造函数存在真实指令变化')
        ctor = ResolverData(cache, WORK)
        patched = bytearray(native)
        gate = int(re.search(r'BlendGateInConstructor = (\d+)', source)[1])
        if native[gate:gate + 2] != b'\x74\x28':
            raise ValueError('混合能力分支变化')
        patched[gate:gate + 2] = b'\x90\x90'
        ctor.add(address, native, bytes(patched))
        # 构造函数以新采样布局作为参考，节内坐标仍对其他缓存重排有效。
        for _, image, rva, _ in ctor.functions[0][3]:
            path, base = ctor.images[image]
            key = path, rva
            segment, section, start, _, hint, index = next(s for s in sections(cache, base) if s[2] <= base + rva < s[2] + s[3])
            value = segment, section, base + rva - start, hint, index
            if segment == "__TEXT":
                continue
            if key in targets and targets[key] != value:
                # 两套参考 RVA 相撞时不能用一份字典解释两种不同布局。
                raise ValueError(f'构造函数参考目标冲突：{path} {rva:#x}')
            targets[key] = value
        ctor_header = ('// 由 tools/build_cache_section_targets.py 生成，不手工编辑。\n#pragma once\n'
                       '#include <UserSurfaceSyncData.hpp>\nnamespace UserSurfaceConstructorData {\n')
        ctor_header += array('Original', b'\x74\x28') + array('Patched', b'\x90\x90')
        ctor_header += f'inline constexpr UserSurfaceSyncData::Patch Patches[] = {{{{0, Original, Patched, 2}}}};\n'
        ctor_header += ctor.header([(0, gate)]) + '}\n'
        submit_address, submit_native = cache.function('-[GFX9AMD_MtlCmdQueue submitCommandBuffers:count:]')
        submit_source = (ROOT / 'NootedRed/UserSequoiaSurfaceData.hpp').read_text().split('namespace Submit {', 1)[1]
        submit_images, submit_functions = metadata(submit_source)
        _, _, submit_original, submit_refs = submit_functions[0]
        ignored = {offset + k for offset, _, _, _ in submit_refs for k in range(4)}
        if len(submit_native) != len(submit_original) or any(
                a != b for i, (a, b) in enumerate(zip(submit_original, submit_native)) if i not in ignored):
            raise ValueError('提交方法存在真实指令变化')
        submit = ResolverData(cache, WORK)
        submit.add(submit_address, submit_native, submit_native)
        for _, image, rva, _ in submit.functions[0][3]:
            path, base = submit.images[image]
            segment, section, start, _, hint, index = next(s for s in sections(cache, base) if s[2] <= base + rva < s[2] + s[3])
            if segment != '__TEXT':
                value = segment, section, base + rva - start, hint, index
                if (path, rva) in targets and targets[path, rva] != value:
                    raise ValueError('提交方法参考目标冲突')
                targets[path, rva] = value
        ctor_header = ctor_header[:-2] + 'namespace Submit {\n' + submit.header([]) + '}\n}\n'
        header = ('// 由 tools/build_cache_section_targets.py 生成，不手工编辑。\n#pragma once\n'
                  'namespace UserCacheSectionData {\nstruct Target {\n'
                  ' const char* path; unsigned long long rva; const char* segment; const char* section;\n'
                  ' unsigned long long offset; unsigned command; unsigned index;\n};\n'
                  'inline constexpr Target Targets[] = {\n')
        for (path, rva), (segment, section, offset, hint, index) in sorted(targets.items()):
            header += f'    {{"{path}", 0x{rva:X}ULL, "{segment}", "{section}", 0x{offset:X}ULL, {hint}, {index}}},\n'
        header += '};\n}\n'
        for name, content in [('UserCacheSectionData.hpp', header), ('UserSurfaceConstructorData.hpp', ctor_header)]:
            path = ROOT / 'NootedRed' / name
            if args.check:
                if path.read_text() != content:
                    raise ValueError(f'生成结果不一致：{name}')
            else:
                path.write_text(content)
        (WORK / 'evidence.json').write_text(json.dumps({'cacheUUID': uuid, 'functions': evidence,
                                                      'targets': len(targets), 'layouts': len(layouts)}, indent=2) + '\n')
        print(f'PASS complete code and reference boundaries; {len(targets)} section targets; {len(layouts)} layouts')
    finally:
        cache.close()


if __name__ == '__main__':
    main()
