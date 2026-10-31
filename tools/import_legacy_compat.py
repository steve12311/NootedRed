"""从固定参考提交提取原始字节；生成有完整页面核验的 Sonoma/Sequoia 适配数据。"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from build_video_decode_patch import identify_payload
from vcn_capabilities import devices
from legacy_cp_factory import factory

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tools/fixtures/legacy-compat.json'
FIXTURE_HEADER = ROOT / 'build/legacy-compat/UserLegacyVideoFixture.hpp'
REVISION = '3b37dfc6282016608962b2746d7809765ad121f4'
SOURCE_HASHES = {
    'VDAVADataSonoma.hpp': '597dd472a7ddc7cd9155d9632f1338558721a063e919d9bf57ac0f041d110f97',
    'VDAGVADataSonoma.hpp': 'cf68c9ad860d27bad23c6e2826f60c482dbe9dd424e8aba5d5e666c1d739330f',
    'UserPatchesSonoma.inc': '5b26e9c8f93b4bb72cc071ccebdde79f5bc1a0d3c7e4a027813432348b9dc540',
    'VDAVADataSequoia.hpp': '2d15e97b0d30c75047c3fe49e18cf5995a3ea6fc6b6b52a85bc454de16639d2d',
    'VDAGVADataSequoia.hpp': '65e91908fb5e5f82631a8ce16c42c1ca7380cc5f573b62f1eb21477b2f9b2eb6',
    'UserPatchesSequoia.inc': '27e2bcb3cfe71c35d565d6147d1f45f41a96b25bb6999958028ee5f6e198f34b',
    'VCNKernelData.hpp': 'de9e3b1313c64888f2c9baaaca1e3988999acde56763c79e64298f6eead61392',
}


def arrays(source):
    return {name: bytes(int(b, 16) for b in re.findall(r'0x([0-9A-Fa-f]{2})\b', body))
            for name, body in re.findall(r'UInt8 (\w+)\[[^\]]*\]\s*=\s*\{(.*?)\};', source, re.S)}


def extract(directory):
    profiles = []
    sources = {}
    for major, os in [(23, 'Sonoma'), (24, 'Sequoia')]:
        names = [f'VDAVAData{os}.hpp', f'VDAGVAData{os}.hpp', f'UserPatches{os}.inc']
        texts = [(directory / name).read_text() for name in names]
        for name, source in zip(names, texts):
            sources[name] = hashlib.sha256(source.encode()).hexdigest()
            if sources[name] != SOURCE_HASHES[name]:
                raise ValueError(f'参考文件与固定提交不符：{name}')
        va, gva, metal = map(arrays, texts)
        pages = []
        for i in range(8):
            # 页面地址由实际补丁 VA 交叉核对，不能使用文件偏移代替共享缓存 VA。
            find_index = [0, 1, 2, 4, 5, 6, 7, 8][i]
            site = re.search(r'// Sites: (.*?)\n\s*inline constexpr UInt8 Find' + str(find_index) + r'\[', texts[0])[1]
            address = int(re.search(r'0x[0-9a-f]+', site)[0], 16)
            page = va[f'Page{i}']
            assert len(page) == 4096 and page[address & 4095:][:len(va[f'Find{find_index}'])] == va[f'Find{find_index}']
            pages.append({'address': address & ~4095, 'bytes': page.hex()})
        sites = []
        for i in range(9):
            line = re.search(r'// Sites: (.*?)\n\s*inline constexpr UInt8 Find' + str(i) + r'\[', texts[0])[1]
            addresses = [int(a, 16) for a in re.findall(r'0x[0-9a-f]+', line.split(' (')[0])]
            sites.append({'addresses': addresses, 'find': va[f'Find{i}'].hex(), 'replace': va[f'Replace{i}'].hex()})
        gva_address = int(re.search(r'patch site (0x[0-9a-f]+)', texts[1])[1], 16)
        uuid = re.search(r'char CacheUUID\[\] = "([^"]+)"', texts[0])[1].replace('-', '').lower()
        gate = [0x7FFB0354D1BC, 0x7FFB087B3164][major - 23]
        allow = [0x7FFB0354D1DB, 0x7FFB087B3185][major - 23]
        profiles.append({'major': major, 'uuid': uuid, 'pages': pages, 'sites': sites,
                         'gva': {'address': gva_address, 'page': gva['Page0'].hex(),
                                 'find': gva['Find0'].hex(), 'replace': gva['Replace0'].hex()},
                         'metal': {'gate': gate, 'allow': allow,
                                   'page': metal[f'kMTLControlPage_{os}'].hex()},
                         'submit': [0x30355E210, 0x3087C3580][major - 23]})
    kernel = (directory / 'VCNKernelData.hpp').read_text()
    sources['VCNKernelData.hpp'] = hashlib.sha256(kernel.encode()).hexdigest()
    if sources['VCNKernelData.hpp'] != SOURCE_HASHES['VCNKernelData.hpp']:
        raise ValueError('内核表与固定参考提交不符')
    data = {'reference': f'https://github.com/weachy/NootedRed/tree/{REVISION}',
            'attribution': 'Reference tables: Copyright (C) 2026 ChefKiss; '
                           'ChefKiss Open Knowledge License version 1. '
                           'Driver bytes: Copyright Apple Inc. and Advanced Micro Devices, Inc.',
            'sources': sources, 'profiles': profiles}
    # 仅提取 ABI 事实，不引入参考分支的初始化、写入或权限处理代码。
    kernel_arrays = arrays(kernel)
    data['getters'] = {n: v.hex() for n, v in kernel_arrays.items()}
    data['kernel'] = {}
    for major, os in [(23, 'Sonoma'), (24, 'Sequoia')]:
        body = re.search(r'CallPatches_' + os + r'\[\] = \{(.*?)\n    \};', kernel, re.S)[1]
        records = re.findall(r'\{\s*"([^"]+)",\s*(0x[0-9A-Fa-f]+),\s*\{([^}]+)\},\s*(0x[0-9A-Fa-f]+),?\s*\}', body)
        data['kernel'][str(major)] = [{'symbol': name, 'offset': int(offset, 16),
                                     'original': bytes(int(b, 16) for b in re.findall(r'0x([0-9A-Fa-f]{2})', raw)).hex(),
                                     'slot': int(slot, 16)} for name, offset, raw, slot in records]
        assert 0 < len(records) <= 32
    FIXTURE.write_text(json.dumps(data, indent=2) + '\n')


def array(name, raw):
    lines = [f'inline constexpr UInt8 {name}[] = {{']
    lines += ['    ' + ', '.join(f'0x{b:02X}' for b in raw[i:i + 16]) + ',' for i in range(0, len(raw), 16)]
    return '\n'.join(lines + ['};\n'])


def generate():
    data = json.loads(FIXTURE.read_text())
    if data['sources'] != SOURCE_HASHES:
        raise ValueError('参考来源摘要不符')
    (ROOT / 'build/vcn').mkdir(parents=True, exist_ok=True)
    capabilities = devices()
    native = arrays((ROOT / 'NootedRed/UserVideoDecodeData.hpp').read_text())
    header = ('// 由 tools/import_legacy_compat.py 生成，不手工编辑。\n'
              '// 参考提交：' + REVISION + ' (weachy/NootedRed)。\n'
              '// ' + data['attribution'] + '\n#pragma once\n#include <UserLegacyCompat.hpp>\n'
              'namespace UserLegacyData {\n')
    # 历史 Video Profile 与 Darwin 24 Metal 只作离线参考；生产头仅保留 Darwin 23 Metal 与提交守卫入口偏移。
    fixture = ('// 由 tools/import_legacy_compat.py 生成，仅供离线测试；不得加入 kext 编译或打包输入。\n'
               '// 参考提交：' + REVISION + ' (weachy/NootedRed)。\n'
               '// ' + data['attribution'] + '\n#pragma once\n#include <UserLegacyData.hpp>\n'
               'namespace UserLegacyFixture {\n')
    for profile in data['profiles']:
        major = profile['major']
        prefix = f'OS{major}'
        production = major == 23
        # Darwin 23 的 UUID 位于生产头；离线 Video Profile 引用同一份数组。
        uuid = f'UserLegacyData::{prefix}UUID' if production else f'{prefix}UUID'
        if production:
            header += array(prefix + 'UUID', bytes.fromhex(profile['uuid']))
        else:
            fixture += array(prefix + 'UUID', bytes.fromhex(profile['uuid']))
        pages = profile['pages'] + [{'address': profile['gva']['address'] & ~4095, 'bytes': profile['gva']['page']}]
        for i, page in enumerate(pages):
            fixture += array(f'{prefix}Page{i}', bytes.fromhex(page['bytes']))
        fixture += f'inline constexpr UserLegacyCompat::Page {prefix}Pages[] = {{\n'
        for i, page in enumerate(pages):
            fixture += f'    {{0x{page["address"] - 0x7FF800000000:X}ULL, {prefix}Page{i}}},\n'
        fixture += '};\n'
        def read(address, size):
            page = next(p for p in pages if p['address'] <= address and address + size <= p['address'] + 4096)
            offset = address - page['address']
            return bytes.fromhex(page['bytes'])[offset:offset + size]
        originals, replacements = [], []
        identify = bytes.fromhex(pages[0]['bytes']).find(native['Original1'][:32])
        assert identify >= 0
        identify_address = pages[0]['address'] + identify
        identify_original = read(identify_address, 448)
        # 三处外部相对引用以外，必须与本地已验证的识别函数完全一致。
        for i, (a, b) in enumerate(zip(identify_original, native['Original1'])):
            assert a == b or any(start <= i < start + 4 for start in [0x27, 0x49, 0x5D])
        originals.append((identify_address, identify_original))
        replacements.append(None)
        for i, site in enumerate(profile['sites'][1:], 1):
            for address in site['addresses']:
                old, new = bytes.fromhex(site['find']), bytes.fromhex(site['replace'])
                assert read(address, len(old)) == old
                if i == 8:
                    old, new = read(address, 160), native['Patched4']
                    assert old == native['Original4']
                elif i == 2:
                    target = 8 + old[-1]
                    new = bytes.fromhex('be02000000eb') + bytes([target - 7]) + b'\x90'
                originals.append((address, old))
                replacements.append(new)
        cp_address, _, _, cp_offset = factory(profile)
        assert read(cp_address + cp_offset, 1) == b'\x04'
        originals.append((cp_address + cp_offset, b'\x04'))
        replacements.append(b'\x05')
        fairplay_mask = (1 << len(originals)) - 1
        gva = profile['gva']
        originals.append((gva['address'], bytes.fromhex(gva['find'])))
        replacements.append(bytes.fromhex(gva['replace']))
        for i, ((address, old), new) in enumerate(zip(originals, replacements)):
            fixture += array(f'{prefix}Original{i}', old)
            if new is not None:
                assert len(old) == len(new)
                fixture += array(f'{prefix}Patched{i}', new)
        for device, family in capabilities:
            variant = f'{prefix}D{device:X}'
            fixture += array(variant + 'Identify', identify_payload(identify_original, device, family))
            fixture += f'inline constexpr UserLegacyCompat::Patch {variant}Patches[] = {{\n'
            for i, (address, old) in enumerate(originals):
                patched = variant + 'Identify' if i == 0 else f'{prefix}Patched{i}'
                fixture += f'    {{0x{address - 0x7FF800000000:X}ULL, {prefix}Original{i}, {patched}, {len(old)}}},\n'
            fixture += '};\n'
            fixture += (f'inline constexpr UserLegacyCompat::Profile {variant} = {{{major}, {uuid}, {prefix}Pages, '
                        f'9, {variant}Patches, {len(originals)}, 0x{fairplay_mask:X}U}};\n')
        metal = profile['metal']
        page = bytes.fromhex(metal['page'])
        gate = metal['gate'] & 4095
        assert page[gate:gate + 2] == b'\x75\x0b'
        text = array(prefix + 'MetalPage', page)
        text += array(prefix + 'Gate', page[gate:gate + 2]) + array(prefix + 'GatePatched', b'\xeb\x0b')
        text += f'inline constexpr UserLegacyCompat::Page {prefix}MetalPages[] = {{{{0x{(metal["gate"] & ~4095) - 0x7FF800000000:X}ULL, {prefix}MetalPage}}}};\n'
        text += f'inline constexpr UserLegacyCompat::Patch {prefix}MetalPatches[] = {{\n'
        for name, size in [('Gate', 2)]:
            address = metal['gate' if name == 'Gate' else 'allow'] - 0x7FF800000000
            text += f'    {{0x{address:X}ULL, {prefix}{name}, {prefix}{name}Patched, {size}}},\n'
        text += '};\n'
        text += f'inline constexpr UserLegacyCompat::Profile {prefix}Metal = {{{major}, {prefix}UUID, {prefix}MetalPages, 1, {prefix}MetalPatches, 1}};\n'
        if production:
            header += text
        else:
            fixture += text
    header += 'inline const UserLegacyCompat::Profile* metal(UInt32 major) { return major == 23 ? &OS23Metal : nullptr; }\n'
    fixture += 'inline const UserLegacyCompat::Profile* metal(UInt32 major) {\n'
    fixture += '    return major == 23 ? UserLegacyData::metal(23) : (major == 24 ? &OS24Metal : nullptr);\n}\n'
    fixture += 'inline const UserLegacyCompat::Profile* video(UInt32 major, UInt32 device) {\n'
    for major in [23, 24]:
        fixture += f'    if (major == {major}) {{ switch (device) {{\n'
        for device, _ in capabilities:
            fixture += f'        case 0x{device:X}: return &OS{major}D{device:X};\n'
        fixture += '        default: return nullptr;\n    } }\n'
    fixture += '    return nullptr;\n}\n'
    fixture += '}\n'
    # 提交守卫入口偏移仍是 Darwin 23/24 DVPF Manifest 的生产输入。
    header += 'inline UInt64 submitOffset(UInt32 major) {\n'
    for profile in data['profiles']:
        header += f"    if (major == {profile['major']}) {{ return 0x{profile['submit']:X}ULL; }}\n"
    header += '    return 0;\n}\n}\n'
    kernel_header = ('// 由 tools/import_legacy_compat.py 生成，不手工编辑。\n'
                     '// ' + data['attribution'] + '\n#pragma once\n#include <VCNKernelData.hpp>\n'
                     'namespace VCNLegacyKernelData {\n')
    for name, raw in data['getters'].items():
        kernel_header += array(name, bytes.fromhex(raw))
    for version in ['5000', '6000']:
        slots = [0x298, 0x2A8, 0x3B8, 0x3C8, 0x3E8, 0x3F0] if version == '5000' else [0x2A0, 0x2B0, 0x3B0, 0x3C0, 0x3E0, 0x3E8]
        kernel_header += f'inline constexpr VCNKernelData::SlotGuard Guards{version}[] = {{\n'
        for i, slot in enumerate(slots):
            if i in [1, 2, 3]:
                name = f'Getter{version}_{i}'
                kernel_header += f'    {{0x{slot:X}, nullptr, {name}, sizeof({name})}},\n'
            else:
                method = {0: 'isDeviceValid', 4: 'disableGfxOff', 5: 'enableGfxOff'}[i]
                cls = 'AMDHardware' if i == 0 else 'AMDRTHardware'
                symbol = f'__ZN{26 if i == 0 else 28}AMDRadeonX{version}_{cls}{len(method)}{method}Ev'
                kernel_header += f'    {{0x{slot:X}, "{symbol}", nullptr, 0}},\n'
        kernel_header += '};\n'
    for major, records in data['kernel'].items():
        kernel_header += f'inline constexpr VCNKernelData::Patch Patches{major}[] = {{\n'
        for patch in records:
            raw = ', '.join(f'0x{b:02X}' for b in bytes.fromhex(patch['original']))
            kernel_header += f'    {{"{patch["symbol"]}", 0x{patch["offset"]:X}, {{{raw}}}, 0x{patch["slot"]:X}}},\n'
        kernel_header += '};\n'
    kernel_header += '}\n'
    return {'UserLegacyData.hpp': header, 'VCNLegacyKernelData.hpp': kernel_header}, fixture


def write_fixture():
    """生成离线历史 Profile 头并返回其目录；只供测试的 -I 搜索路径使用。"""
    _, fixture = generate()
    FIXTURE_HEADER.parent.mkdir(parents=True, exist_ok=True)
    if not FIXTURE_HEADER.exists() or FIXTURE_HEADER.read_text() != fixture:
        FIXTURE_HEADER.write_text(fixture)
    return FIXTURE_HEADER.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--import-reference', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.import_reference:
        extract(args.import_reference)
    production, _ = generate()
    for name, content in production.items():
        path = ROOT / 'NootedRed' / name
        if args.check:
            assert path.read_text() == content, f'生成数据过期：{path}'
        else:
            path.write_text(content)
    write_fixture()
    print('PASS legacy compatibility data: exact pages, PCI-specific identify, RV address path and ABI tables; '
          'historical video profiles generated to ' + str(FIXTURE_HEADER.relative_to(ROOT)))


if __name__ == '__main__':
    main()
