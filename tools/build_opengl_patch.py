"""从完整原始页面生成 OpenGL 条件修复；只读核验本机 15/26 文件，不写系统驱动。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import plistlib
import struct

from metal_cache import array, macho
from build_compute_scratch_patch import supported_devices

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/opengl'
DRIVER = 'System/Library/Extensions/AMDRadeonX5000GLDriver.bundle/Contents/MacOS/AMDRadeonX5000GLDriver'
PAGE_HASHES = {
    23: ['fd867140ac07edfcabeeba58baf702195befb82a6a78b8d6a19bcfc5aab9754a',
         '4265dc29fe2b1df22f03ae9d0ca563ed90d998ea10cef4fc68b9bc50ba788a09'],
    24: ['94fca357fefd7de0508910c0d9c42e9a1a771760b05242ec543cbf1b775e5af9',
         'e8a5f32bb90c9e5bd130d6bc490410f16b76183ea02e87c3eb77b002bc7da932'],
    25: ['8871af2e3a830368d848c68e5f588ee73cbb331601b9441547806ca517f81610',
         'e5165b5b7dd641a1f3d886aec0a87864f9d3763cadd76d5528e572c0f45ed705'],
}


def payload(template, fields, label):
    source = (ROOT / 'tools' / template).read_text()
    for name, value in fields.items():
        source = source.replace(name, hex(value))
    asm, obj = WORK / f'{label}.s', WORK / f'{label}.o'
    asm.write_text(source)
    subprocess.run(['clang', '-c', str(asm), '-o', str(obj)], check=True, timeout=60)
    data = obj.read_bytes()
    _, sections, _ = macho(lambda a, n: data[a:a+n], 0)
    text = next(s for s in sections if s[0] == '__text')
    return data[text[3]:text[3]+text[2]]


def generate(verify_local=False):
    WORK.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((ROOT / 'tools/fixtures/opengl-compat.json').read_text())
    header = ('// 由 tools/build_opengl_patch.py 生成，不手工编辑。\n'
              '// Driver bytes: Copyright Apple Inc. and Advanced Micro Devices, Inc.\n'
              '#pragma once\n#include <IOKit/IOTypes.h>\n#include <UserCacheResolver.hpp>\nnamespace UserOpenGLData {\n'
              'struct Page { const UInt8* original; const UInt8* patched; UInt32 offset, size; };\n'
              'struct Patch { UInt64 offset; const UInt8* original; const UInt8* patched; UInt32 size; };\n'
              'struct Resolver { UserCacheResolver::Function functions[2]; UserCacheResolver::Location locations[2]; };\n'
              'struct Device { UInt32 major, deviceID; const Page* pages; const Resolver* resolver; const Patch* patches; };\n')
    devices, reports, verified = [], [], []
    for profile in fixture['profiles']:
        major = profile['major']
        pages = [bytes.fromhex(p['bytes']) for p in profile['pages']]
        assert [hashlib.sha256(p).hexdigest() for p in pages] == PAGE_HASHES[major]
        local = None
        if verify_local and major in (24, 25):
            root = Path('/Volumes/Macintosh SSD') if major == 24 else Path('/')
            path = root / DRIVER
            local = path.read_bytes()
            for offset, page in zip([0xe000, 0x8000], pages):
                if local[offset:offset+4096] != page:
                    raise ValueError(f'{path} 的 {offset:#x} 页面未核实')
            system = plistlib.loads((root / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
            verified.append({'path': str(path), 'version': system['ProductVersion'], 'build': system['ProductBuildVersion'],
                             'driverSHA256': hashlib.sha256(local).hexdigest(),
                             'pages': {hex(offset): hashlib.sha256(page).hexdigest()
                                       for offset, page in zip([0xe000, 0x8000], pages)}})
        hw, dual = profile['hw'], profile['dual']
        for f in [hw, dual]:
            native = bytes.fromhex(f['native'])
            assert hashlib.sha256(native).hexdigest() == f['sha256']
            if local is not None:
                assert local[f['address']:f['address']+len(native)] == native
        assert pages[0][hw['tailOffset']:hw['tailOffset']+256] == bytes.fromhex(hw['native'])[0x128:]
        assert pages[1][dual['offset']:dual['offset']+142] == bytes.fromhex(dual['native'])
        for i, page in enumerate(pages):
            header += array(f'Page{major}_{i}', page)
        header += array(f'NativeHw{major}', bytes.fromhex(hw['native']))
        header += array(f'NativeDual{major}', bytes.fromhex(dual['native']))
        for device in supported_devices():
            label = f'OS{major}PCI{device:04X}'
            fixed_hw = payload('opengl_hwinfo_tail.s.in', {'DEVICE': device, 'ADDRESS': hw['address']+0x128,
                               'CONST0': hw['constants'][0], 'CONST1': hw['constants'][1]}, label+'Hw')
            fixed_dual = payload('opengl_dual_quad.s.in', {'DEVICE': device, 'ADDRESS': dual['address'],
                                                        'SUBMIT': dual['submit']}, label+'Dual')
            assert len(fixed_hw) == 256 and len(fixed_dual) == 142
            header += array(label+'Hw', fixed_hw) + array(label+'Dual', fixed_dual)
            header += f'inline constexpr Page {label}[] = {{\n'
            header += f'    {{Page{major}_0, {label}Hw, {hw["tailOffset"]}, 256}},\n'
            header += f'    {{Page{major}_1, {label}Dual, {dual["offset"]}, 142}}\n}};\n'
            header += f'inline constexpr Patch {label}Patches[] = {{\n'
            header += f'    {{0x{hw["address"]+0x128:X}, Page{major}_0+{hw["tailOffset"]}, {label}Hw, 256}},\n'
            header += f'    {{0x{dual["address"]:X}, NativeDual{major}, {label}Dual, 142}}\n}};\n'
            devices.append(f'    {{{major}, 0x{device:X}, {label}, &Resolver{major}, {label}Patches}}')
            reports.append({'major': major, 'deviceID': device, 'hw': {**hw, 'patched': fixed_hw.hex()},
                            'dual': {**dual, 'patched': fixed_dual.hex()}})
        def references(code, address, kind):
            offsets = ([i+4 for i in range(len(code)-7) if code.startswith(bytes.fromhex('c5f82805'), i)] if kind == 'Hw' else
                       [i+1 for i in range(len(code)-4) if code[i] == 0xe8])
            assert len(offsets) == (3 if kind == 'Hw' else 1)
            return [(o, address+o+4+struct.unpack_from('<i', code, o)[0]) for o in offsets]
        for kind, meta, fixed in [('Hw', hw, bytes.fromhex(hw['native'])[:0x128]+fixed_hw), ('Dual', dual, fixed_dual)]:
            for variant, code in [('Original', bytes.fromhex(meta['native'])), ('Patched', fixed)]:
                header += f'inline constexpr UserCacheResolver::Reference {variant}{kind}Refs{major}[] = {{\n'
                header += ''.join(f'    {{{o}, 0, 0x{target:X}, 0}},\n' for o, target in references(code, meta['address'], kind))+'};\n'
        header += f'inline constexpr Resolver Resolver{major} = {{{{\n'
        for kind, meta, size, count in [('Hw', hw, 552, 3), ('Dual', dual, 142, 1)]:
            header += f'    {{0, 0x{meta["address"]:X}, Native{kind}{major}, {size}, Original{kind}Refs{major}, {count}, Patched{kind}Refs{major}, {count}}},\n'
        header += '}, {{0, 0x128}, {1, 0}}};\n'
    header += 'inline constexpr Device Devices[] = {\n'+',\n'.join(devices)+'\n};\n'
    header += ('inline const Device* findDevice(UInt32 major, UInt32 deviceID) {\n'
               '    for (const auto& e : Devices) { if (e.major == major && e.deviceID == deviceID) { return &e; } }\n'
               '    return nullptr;\n}\n}\n')
    (WORK / 'patches.json').write_text(json.dumps(reports, indent=2)+'\n')
    if verify_local:
        (WORK / 'verified-pages.json').write_text(json.dumps(verified, indent=2)+'\n')
    return header


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-local', action='store_true')
    args = parser.parse_args()
    header = generate(args.verify_local)
    out = ROOT / 'NootedRed/UserOpenGLData.hpp'
    if args.check:
        assert out.read_text() == header, 'OpenGL 数据不可复现'
    else:
        out.write_text(header)
    print('PASS OpenGL data:', len(header), 'bytes; 3 systems, 6 APU identities; local verification:', args.verify_local)
