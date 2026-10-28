"""DVPF：OpenGL 驱动只读证据、参考页与确定性 GLManifestData。

工具只读取输入根内的真实 GL 可执行文件；挂载卷路径、SHA-256、Build、UUID 和
相对目标都写入报告。运行时仍由 `UserOpenGLResolver` 在当前页内重新匹配，不把
离线文件偏移当作固定补丁位置。
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import struct
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
OPENGL_HEADER = ROOT / 'NootedRed/UserOpenGLData.hpp'
OPENGL_FIXTURE = ROOT / 'tools/fixtures/opengl-compat.json'
DRIVER_PATH = ('System/Library/Extensions/AMDRadeonX5000GLDriver.bundle/Contents/MacOS/'
               'AMDRadeonX5000GLDriver')
FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
MASK64 = (1 << 64) - 1
MAX_DRIVER_SIZE = 64 * 1024 * 1024
MAX_BLOCK_CANDIDATES = 16


class EvidenceError(ValueError):
    pass


def fnv1a(data: bytes, skip: Iterable[int] = ()) -> int:
    """与 C++ GLManifestData::digest 完全一致；skip 字节按 0 处理。"""
    masked = set(skip)
    value = FNV_OFFSET
    for index, byte in enumerate(data):
        if index in masked:
            byte = 0
        value ^= byte
        value = (value * FNV_PRIME) & MASK64
    return value


def _ref_skip(refs: list[dict], start: int, size: int) -> set[int]:
    result: set[int] = set()
    for ref in refs:
        relative = ref['offset'] - start
        if relative < 0 or relative + 4 > size:
            raise EvidenceError(f'引用超出补丁块: offset={ref["offset"]} start={start} size={size}')
        result.update(range(relative, relative + 4))
    return result


def _feature_major(feature: dict) -> int:
    return int(feature['os'][0].replace('Darwin', ''))


def _ref_name(feature: dict) -> str:
    return 'Hw' if feature['feature'] == 'OpenGLHwInfo' else 'Dual'


def load_fixture_profiles() -> dict[int, dict]:
    document = json.loads(OPENGL_FIXTURE.read_text())
    return {profile['major']: profile for profile in document['profiles']}


def build_fixture_evidence() -> list[dict]:
    """从仓库内固定夹具生成生产证据表；不读取系统缓存或驱动文件。"""
    import display_data

    features = [feature for feature in display_data.build_all()
                if feature['domain'] == 'OpenGLValidatedPage']
    refs = display_data.reference_arrays(OPENGL_HEADER)
    profiles = load_fixture_profiles()
    result = []
    for feature in features:
        major = _feature_major(feature)
        profile = profiles[major]
        site = feature['sites'][0]
        kind = site['functionIndex']
        name = _ref_name(feature)
        page = bytes.fromhex(profile['pages'][kind]['bytes'])
        original = site['original']
        patched = site['replacement']
        start = site['instructionOffset']
        original_refs = refs[f'Original{name}Refs{major}']
        patched_refs = refs[f'Patched{name}Refs{major}']
        result.append(
            {
                'major': major,
                'deviceID': feature['deviceID'],
                'kind': kind,
                'blockSize': site['size'],
                'pageFileOffset': site['pageFileOffset'],
                'blockOffsetInPage': site['pageOffset'],
                'functionIndex': site['functionIndex'],
                'instructionOffset': start,
                'pageDigest': fnv1a(page),
                'originalDigest': fnv1a(original, _ref_skip(original_refs, start, len(original))),
                'patchedDigest': fnv1a(patched, _ref_skip(patched_refs, start, len(patched))),
                'original': original,
                'patched': patched,
                'cppPage': f'UserOpenGLData::Page{major}_{kind}',
                'cppPatch': site['cppPatch'],
                'source': 'opengl-compat.json',
                'build': profile['os'],
                'sourceSHA': profile['sourceSHA'],
            }
        )
    result.sort(key=lambda item: (item['major'], item['deviceID'], item['kind']))
    if len(result) != 36 or len({(item['major'], item['deviceID'], item['kind']) for item in result}) != 36:
        raise EvidenceError('生产 GL 证据表必须覆盖 3 个 Darwin × 6 个设备 × 2 个功能')
    return result


def _cstr(value: str | None) -> str:
    if value is None:
        return 'nullptr'
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def emit_cpp(evidence: list[dict]) -> str:
    lines = [
        '// 由 tools/vpf/build_gl_evidence.py --fixtures --write 生成，不手工编辑。',
        '// 只记录参考页/补丁块证据；运行时仍由 UserOpenGLResolver 动态核验。',
        '#pragma once',
        '#include <UserOpenGLData.hpp>',
        '',
        'namespace VPF',
        '{',
        'namespace GLManifestData',
        '{',
        '',
        'struct PageEvidence',
        '{',
        '    UInt32 major, deviceID, kind, blockSize;',
        '    UInt64 pageFileOffset, blockOffsetInPage;',
        '    UInt32 functionIndex, instructionOffset;',
        '    UInt64 pageDigest, originalDigest, patchedDigest;',
        '    const UInt8* page;',
        '    const UInt8* original;',
        '    const UInt8* patched;',
        '    const char* source;',
        '    const char* build;',
        '    const char* sourceSHA;',
        '};',
        '',
        'inline constexpr PageEvidence Evidence[] = {',
    ]
    for item in evidence:
        lines.append(
            f'    {{{item["major"]}, 0x{item["deviceID"]:04X}, {item["kind"]}, {item["blockSize"]},\n'
            f'     0x{item["pageFileOffset"]:X}ULL, 0x{item["blockOffsetInPage"]:X}ULL, '
            f'{item["functionIndex"]}, 0x{item["instructionOffset"]:X},\n'
            f'     0x{item["pageDigest"]:016X}ULL, 0x{item["originalDigest"]:016X}ULL, '
            f'0x{item["patchedDigest"]:016X}ULL,\n'
            f'     {item["cppPage"]}, {item["cppPatch"]}.original, {item["cppPatch"]}.patched,\n'
            f'     {_cstr(item["source"])}, {_cstr(item["build"])}, {_cstr(item["sourceSHA"])}}},'
        )
    lines += [
        '};',
        'inline constexpr UInt32 EvidenceCount = sizeof(Evidence) / sizeof(Evidence[0]);',
        'static_assert(EvidenceCount == 36);',
        '',
        'constexpr const PageEvidence* find(const UInt32 major, const UInt32 deviceID, const UInt32 kind)',
        '{',
        '    for (UInt32 i = 0; i < EvidenceCount; ++i) {',
        '        const auto& entry = Evidence[i];',
        '        if (entry.major == major && entry.deviceID == deviceID && entry.kind == kind) { return &entry; }',
        '    }',
        '    return nullptr;',
        '}',
        '',
        '// 只对补丁块计算；rel32 字段按 0 处理，避免文件地址变化影响证据摘要。',
        'inline UInt64 digest(const UInt8* bytes, const UInt32 size, const UserCacheResolver::Reference* refs,',
        '                     const UInt32 count, const UInt32 start)',
        '{',
        '    UInt64 value = 14695981039346656037ULL;',
        '    for (UInt32 i = 0; i < size; ++i) {',
        '        UInt8 byte = bytes[i];',
        '        if (UserCacheResolver::skipped(start + i, refs, count)) { byte = 0; }',
        '        value ^= byte;',
        '        value *= 1099511628211ULL;',
        '    }',
        '    return value;',
        '}',
        '',
        '}    // namespace GLManifestData',
        '}    // namespace VPF',
        '',
    ]
    return '\n'.join(lines)


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]


def parse_macho(data: bytes) -> dict:
    if len(data) < 32:
        raise EvidenceError('Mach-O 文件过短')
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = struct.unpack_from(
        '<IiiIIIII', data, 0)
    if magic != 0xFEEDFACF:
        raise EvidenceError('不是 64 位 Mach-O')
    if ncmds == 0 or ncmds > 128 or sizeofcmds > 65536 or 32 + sizeofcmds > len(data):
        raise EvidenceError('Mach-O load command 范围非法')
    result = {'cputype': cputype, 'cpusubtype': cpusubtype, 'filetype': filetype, 'uuid': None,
              'sourceVersion': None, 'buildVersion': None, 'segments': [], 'sections': []}
    cursor = 32
    for _ in range(ncmds):
        if cursor + 8 > 32 + sizeofcmds:
            raise EvidenceError('Mach-O load command 越界')
        command, length = struct.unpack_from('<II', data, cursor)
        if length < 8 or cursor + length > 32 + sizeofcmds or cursor + length > len(data):
            raise EvidenceError('Mach-O load command 长度非法')
        if command == 0x19:
            name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, segflags = struct.unpack_from(
                '<16sQQQQiiII', data, cursor + 8)
            segment = {'name': name.split(b'\0', 1)[0].decode(errors='replace'), 'vmaddr': vmaddr,
                       'vmsize': vmsize, 'fileoff': fileoff, 'filesize': filesize,
                       'maxprot': maxprot, 'initprot': initprot, 'flags': segflags}
            result['segments'].append(segment)
            for index in range(nsects):
                section_offset = cursor + 72 + index * 80
                if section_offset + 80 > cursor + length:
                    raise EvidenceError('Mach-O section 越界')
                sectname, segname, addr, size, offset, align, reloff, nreloc, secflags, _, _, _ = struct.unpack_from(
                    '<16s16sQQIIIIIIII', data, section_offset)
                if offset > len(data) or size > len(data) - offset:
                    raise EvidenceError('Mach-O section 文件范围非法')
                result['sections'].append(
                    {'name': sectname.split(b'\0', 1)[0].decode(errors='replace'),
                     'segment': segname.split(b'\0', 1)[0].decode(errors='replace'),
                     'addr': addr, 'size': size, 'offset': offset, 'align': align,
                     'reloff': reloff, 'nreloc': nreloc, 'flags': secflags}
                )
        elif command == 0x1B and length >= 24:
            result['uuid'] = data[cursor + 8:cursor + 24].hex().upper()
        elif command == 0x2A and length >= 16:
            result['sourceVersion'] = struct.unpack_from('<Q', data, cursor + 8)[0]
        elif command == 0x32 and length >= 24:
            platform, minos, sdk, ntools = struct.unpack_from('<IIII', data, cursor + 8)
            result['buildVersion'] = {'platform': platform, 'minos': minos, 'sdk': sdk, 'ntools': ntools}
        elif command == 0x24 and length >= 16:
            version, sdk = struct.unpack_from('<II', data, cursor + 8)
            result['buildVersion'] = {'platform': 1, 'minos': version, 'sdk': sdk, 'ntools': 0}
        cursor += length
    if cursor != 32 + sizeofcmds:
        raise EvidenceError('Mach-O load command 未精确结束')
    text = next((segment for segment in result['segments'] if segment['name'] == '__TEXT'), None)
    if text is None or text['filesize'] == 0:
        raise EvidenceError('Mach-O 缺少 __TEXT 段')
    if text['fileoff'] > len(data) or text['filesize'] > len(data) - text['fileoff']:
        raise EvidenceError('__TEXT 文件范围非法')
    result['text'] = text
    return result


def section_for(macho: dict, offset: int) -> dict | None:
    for section in macho['sections']:
        if section['offset'] <= offset < section['offset'] + section['size']:
            return section
    return None


def _anchor(pattern: bytes, mask: list[bool]) -> tuple[int, bytes] | None:
    best_start, best_length = 0, 0
    index = 0
    while index < len(pattern):
        if mask[index]:
            index += 1
            continue
        start = index
        while index < len(pattern) and not mask[index]:
            index += 1
        if index - start > best_length:
            best_start, best_length = start, index - start
    if best_length < 8:
        return None
    return best_start, pattern[best_start:best_start + best_length]


def masked_positions(data: bytes, pattern: bytes, mask: list[bool]) -> list[int]:
    found = _anchor(pattern, mask)
    if found is None:
        raise EvidenceError('补丁块缺少可用于定位的非 rel32 锚点')
    anchor_start, anchor = found
    result = []
    cursor = data.find(anchor)
    while cursor != -1:
        start = cursor - anchor_start
        if start >= 0 and start + len(pattern) <= len(data):
            if all(mask[index] or data[start + index] == pattern[index] for index in range(len(pattern))):
                result.append(start)
                if len(result) > MAX_BLOCK_CANDIDATES:
                    break
        cursor = data.find(anchor, cursor + 1)
    return result


def _fixture_block(profile: dict, kind: int) -> tuple[bytes, int, int, int]:
    page = bytes.fromhex(profile['pages'][kind]['bytes'])
    if kind == 0:
        block_offset = profile['hw']['tailOffset']
        size = 256
        start = 0x128
        page_file_offset = profile['hw']['address'] + 0x128 - block_offset
    else:
        block_offset = profile['dual']['offset']
        size = 142
        start = 0
        page_file_offset = profile['dual']['address'] - block_offset
    return page[block_offset:block_offset + size], page, block_offset, page_file_offset


def _fixture_refs(kind: int, major: int, patched: bool) -> list[dict]:
    import display_data

    refs = display_data.reference_arrays(OPENGL_HEADER)
    name = 'Hw' if kind == 0 else 'Dual'
    return refs[f'{"Patched" if patched else "Original"}{name}Refs{major}']


def _ref_layout(refs: list[dict], start: int, size: int) -> list[bool]:
    mask = [False] * size
    for ref in refs:
        relative = ref['offset'] - start
        if relative < 0 or relative + 4 > size:
            raise EvidenceError('引用超出补丁块')
        for index in range(relative, relative + 4):
            mask[index] = True
    return mask


def inspect_system_root(root: Path, darwin: int | None = None) -> dict:
    """只读采集挂载卷；不跟随逃逸输入根的符号链接。"""
    root = root.resolve()
    if not root.is_dir():
        raise EvidenceError(f'系统根不存在或不是目录: {root}')
    version_path = (root / 'System/Library/CoreServices/SystemVersion.plist').resolve()
    driver_path = (root / DRIVER_PATH).resolve()
    for path in (version_path, driver_path):
        if not path.is_relative_to(root):
            raise EvidenceError(f'符号链接逃逸输入根: {path}')
        if not path.is_file():
            raise EvidenceError(f'缺少只读输入: {path}')
    if driver_path.stat().st_size > MAX_DRIVER_SIZE:
        raise EvidenceError('GL 驱动超过只读分析上限')
    version = plistlib.loads(version_path.read_bytes())
    major = darwin if darwin is not None else _darwin_from_product(version.get('ProductVersion', ''))
    data = driver_path.read_bytes()
    macho = parse_macho(data)
    if macho['cputype'] != 0x01000007:
        raise EvidenceError(f'GL 驱动不是 x86_64: cputype={macho["cputype"]:#x}')
    profiles = load_fixture_profiles()
    if major not in profiles:
        raise EvidenceError(f'没有 Darwin{major} 的参考夹具')
    profile = profiles[major]
    fixture = build_fixture_evidence()
    fixture_by_kind = {
        kind: [item for item in fixture if item['major'] == major and item['kind'] == kind]
        for kind in (0, 1)
    }
    result = {
        'schemaVersion': 1,
        'kind': 'vpf.gl-evidence',
        'source': {
            'driverPath': DRIVER_PATH,
            'productVersion': version.get('ProductVersion'),
            'buildVersion': version.get('ProductBuildVersion'),
            'darwin': major,
            'arch': 'x86_64',
            'fileSize': len(data),
            'fileSHA256': hashlib.sha256(data).hexdigest(),
            'uuid': macho['uuid'],
            'sourceVersion': macho['sourceVersion'],
            'fileType': macho['filetype'],
            'machoBuildVersion': macho['buildVersion'],
            'readOnly': True,
        },
        'evidence': [],
        'oldNewEquivalence': [],
        'limitations': [
            '离线证据只证明当前文件中的完整页/完整块和相对目标；不证明该文件已被当前内核加载或 GPU 已执行。',
            '运行时仍由 UserOpenGLResolver 在当前 4 KiB 页内重新匹配；文件偏移不是固定补丁位置。',
            '没有挂载卷时只能生成工具和仓库内 fixtures 的结果，不能声称已验证该版本。',
        ],
    }
    all_blocks = True
    for kind, feature in ((0, 'OpenGLHwInfo'), (1, 'OpenGLDualQuad')):
        block, page, block_offset, page_file_offset = _fixture_block(profile, kind)
        original_refs = _fixture_refs(kind, major, False)
        patched_refs = _fixture_refs(kind, major, True)
        mask = _ref_layout(original_refs, 0x128 if kind == 0 else 0, len(block))
        positions = masked_positions(data, block, mask)
        entry = {'feature': feature, 'major': major, 'kind': kind, 'blockSize': len(block),
                 'blockOffsetInPage': block_offset, 'pageFileOffset': page_file_offset,
                 'candidateCount': len(positions), 'status': 'missing',
                 'referencePageMatched': False, 'runtimeCompatible': False}
        if len(positions) == 1:
            pos = positions[0]
            page_base = pos & ~4095
            page_bytes = data[page_base:page_base + 4096]
            start = 0x128 if kind == 0 else 0
            refs = []
            for ref in original_refs:
                relative = ref['offset'] - start
                displacement = struct.unpack_from('<i', data, pos + relative)[0]
                target = pos + relative + 4 + displacement
                section = section_for(macho, target)
                refs.append({'offset': ref['offset'], 'rva': ref['rva'], 'target': target,
                             'targetSection': section['segment'] + ',' + section['name'] if section else None,
                             'targetInFile': section is not None,
                             'referenceTargetMatched': target == ref['rva']})
            observed_original = fnv1a(data[pos:pos + len(block)], _ref_skip(original_refs, start, len(block)))
            reference_original = fixture_by_kind[kind][0]['originalDigest'] if fixture_by_kind[kind] else None
            entry.update(
                {
                    'status': 'matched' if all(item['referenceTargetMatched'] for item in refs) else 'mismatch',
                    'blockFileOffset': pos,
                    'pageFileOffset': page_base,
                    'blockOffsetInPage': pos & 4095,
                    'pageSHA256': hashlib.sha256(page_bytes).hexdigest(),
                    'referencePageMatched': page_bytes == page,
                    'pageDigest': fnv1a(page_bytes),
                    'originalDigest': observed_original,
                    'referenceOriginalDigest': reference_original,
                    'referencePatchedDigests': {
                        item['deviceID']: item['patchedDigest'] for item in fixture_by_kind[kind]
                    },
                    'refs': refs,
                }
            )
            entry['runtimeCompatible'] = (
                entry['status'] == 'matched' and observed_original == reference_original
            )
            if not entry['runtimeCompatible']:
                all_blocks = False
        elif len(positions) == 0:
            all_blocks = False
        else:
            entry.update({'status': 'ambiguous', 'positions': positions})
            all_blocks = False
        result['evidence'].append(entry)
        for reference in fixture_by_kind[kind]:
            result['oldNewEquivalence'].append(
                {
                    'feature': feature,
                    'major': major,
                    'deviceID': reference['deviceID'],
                    'originalSHA256': hashlib.sha256(reference['original']).hexdigest(),
                    'patchedSHA256': hashlib.sha256(reference['patched']).hexdigest(),
                    'patchedDigest': reference['patchedDigest'],
                    'changedBytes': sum(1 for left, right in zip(reference['original'], reference['patched'])
                                        if left != right),
                    'sameSize': len(reference['original']) == len(reference['patched']),
                }
            )
    result['allReferenceBlocksFound'] = all_blocks
    return result


def _darwin_from_product(product: str) -> int:
    try:
        major = int(product.split('.', 1)[0])
    except (ValueError, AttributeError) as error:
        raise EvidenceError(f'无法解析 macOS 版本: {product!r}') from error
    mapping = {14: 23, 15: 24, 26: 25}
    if major not in mapping:
        raise EvidenceError(f'未支持的 macOS 主版本: {major}；请显式提供 --darwin')
    return mapping[major]


def proof_document(result: dict) -> dict:
    return result


def variants_document(result: dict) -> dict:
    return {
        'schemaVersion': 1,
        'kind': 'vpf.gl-variants',
        'source': result['source'],
        'emittedManifests': 0,
        'variants': result['evidence'],
        'oldNewEquivalence': result['oldNewEquivalence'],
        'limitations': result['limitations'],
    }


def manifest_data_document(result: dict) -> dict:
    fixture = build_fixture_evidence()
    entries = []
    for entry in result['evidence']:
        if entry['status'] != 'matched':
            continue
        for reference in fixture:
            if (reference['major'], reference['kind']) != (entry['major'], entry['kind']):
                continue
            entries.append(
                {
                    'major': reference['major'],
                    'deviceID': reference['deviceID'],
                    'kind': reference['kind'],
                    'blockSize': reference['blockSize'],
                    'pageFileOffset': reference['pageFileOffset'],
                    'blockOffsetInPage': reference['blockOffsetInPage'],
                    'functionIndex': reference['functionIndex'],
                    'instructionOffset': reference['instructionOffset'],
                    'pageDigest': reference['pageDigest'],
                    'originalDigest': reference['originalDigest'],
                    'patchedDigest': reference['patchedDigest'],
                    'source': 'fixture-reference',
                    'observed': {
                        'pageFileOffset': entry['pageFileOffset'],
                        'blockOffsetInPage': entry['blockOffsetInPage'],
                        'pageDigest': entry['pageDigest'],
                        'originalDigest': entry['originalDigest'],
                        'referencePageMatched': entry['referencePageMatched'],
                    },
                }
            )
    entries.sort(key=lambda item: (item['major'], item['deviceID'], item['kind']))
    return {
        'schemaVersion': 1,
        'kind': 'vpf.gl-manifest-data',
        'source': result['source'],
        'entries': entries,
        'limitations': result['limitations'],
    }
