"""核验 Sequoia 完整函数，复用 SRD/Scratch 载荷及公共动态解析数据生成器。"""
import argparse
import hashlib
import json
import struct
import subprocess
from pathlib import Path

from metal_cache import CacheReader, array, macho
from cache_resolver_data import ResolverData
from build_compute_scratch_patch import generate, supported_devices, SYMBOL

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/sequoia/surface'
INIT = '__Z29amdMtl_HWL_InitDeviceSettingsP18AMD_DeviceSettingsP18AMDCommonHwInfoRec'
OVERRIDE = '__Z33amdMtl_HWL_OverrideDeviceSettingsP18AMD_DeviceSettingsP18AMDCommonHwInfoRec'
SETTINGS = '__Z29amdMtl_OverrideDeviceSettingsP18AMD_DeviceSettings'
CONSTRUCTOR = '-[GFX9_MtlRenderPipelineState initWithDevice:pipelineStateDescriptor:vertexVariant:fragmentVariant:]'
SUBMIT = '-[GFX9AMD_MtlCmdQueue submitCommandBuffers:count:]'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads((ROOT / 'tools/fixtures/sequoia-compat.json').read_text())
    WORK.mkdir(parents=True, exist_ok=True)
    cache = CacheReader(args.cache, profile['uuid'])
    try:
        functions = {}
        for name, meta in profile['metal'].items():
            address, code = cache.function(name)
            if len(code) != meta['size'] or hashlib.sha256(code).hexdigest() != meta['sha256']:
                raise ValueError(f'Sequoia 原生函数未核实：{name}')
            functions[name] = address, code
        resolver = ResolverData(cache, WORK)
        init, native = functions[INIT]
        refs = resolver.references(native, init, 'init')
        if [r[0] for r in refs] != [135, 197, 211] or any(r[3] for r in refs):
            raise ValueError('初始化外部引用布局变化')
        targets = [resolver.images[i][1] + rva for _, i, rva, _ in refs]
        if targets[1] != functions[SETTINGS][0] or cache.read(targets[0], 4) != struct.pack('<f', 3.0):
            raise ValueError('初始化目标或常量未核实')
        assembly = (ROOT / 'tools/surface_sync_srd_shared_init.s').read_text()
        for old, new in zip([0x7ffb10fbfde8, 0x7ffb10c5422b, 0x7ffb10c545c6, 0x7ffb10d71a28],
                            [*targets, init]):
            assembly = assembly.replace(hex(old), hex(new))
        source, obj = WORK / 'init.s', WORK / 'init.o'
        source.write_text(assembly)
        subprocess.run(['clang', '-c', str(source), '-o', str(obj)], check=True, timeout=60)
        data = obj.read_bytes()
        _, sections, _ = macho(lambda a, n: data[a:a+n], 0)
        section = next(s for s in sections if s[0] == '__text')
        payload = data[section[3]:section[3]+section[2]]
        if len(payload) != len(native):
            raise ValueError('SRD 载荷越界')
        override, override_native = functions[OVERRIDE]
        override_payload = b'\xe9' + struct.pack('<i', init + 166 - override - 5) + b'\x90'*5
        legacy = json.loads((ROOT / 'tools/fixtures/legacy-compat.json').read_text())['profiles'][1]
        gate = legacy['metal']['gate']
        if cache.read(gate, 2) != b'\x75\x0b':
            raise ValueError('LegacyBlend 能力 gate 未核实')
        # 短覆盖入口连同下一完整函数核验；补丁仍仅改入口本身。
        end = min(a for a, _ in cache.symbols if a > override + len(override_native))
        override_context = cache.read(override, end-override)
        if hashlib.sha256(override_context).hexdigest() != 'a13abd072bb29eb62c3cac0a42fdd5f469c7f593fd35ac9e278f3bb5a173588e':
            raise ValueError('覆盖入口的完整上下文变化')
        records = [(init, native, payload), (override, override_native, override_payload),
                   (gate, b'\x75\x0b', b'\xeb\x0b')]
        scratch = generate(cache, profile=profile['metal'][SYMBOL])
        records.append((scratch['address'], bytes.fromhex(scratch['original']), bytes.fromhex(scratch['patched'])))
        ctor, ctor_native = functions[CONSTRUCTOR]
        ctor_patched = bytearray(ctor_native)
        ctor_patched[gate-ctor:gate-ctor+2] = b'\xeb\x0b'
        resolver.add(init, native, payload)
        resolver.add(override, override_context, override_payload + override_context[10:])
        resolver.add(ctor, ctor_native, bytes(ctor_patched))
        resolver.add(records[3][0], records[3][1], records[3][2])
        resolver.add(*functions[SETTINGS], functions[SETTINGS][1])
        locations = [(0, 0), (1, 0), (2, gate-ctor), (3, 0)]
        header = ('// 由 tools/build_sequoia_surface.py 生成，不手工编辑。\n#pragma once\n'
                  '#include <UserSurfaceSyncData.hpp>\nnamespace UserSequoiaSurfaceData {\n'
                  'using Patch = UserSurfaceSyncData::Patch;\n')
        header += array('CacheUUID', cache.uuid)
        header += 'inline constexpr UInt32 MaxPatchSize = 456;\n'
        for i, (_, original, patched) in enumerate(records):
            header += array(f'Original{i}', original) + array(f'Patched{i}', patched)
        device_records = []
        for device in supported_devices():
            name = f'Patches{device:04X}'
            variant = payload.replace(bytes.fromhex('817e0c38160000'), bytes.fromhex('817e0c') + struct.pack('<I', device))
            patched_scratch = bytes.fromhex(generate(cache, device, profile['metal'][SYMBOL])['patched'])
            variants = [variant, override_payload, b'\xeb\x0b', patched_scratch]
            device_records.append({'device_id': device, 'records': [
                {'label': label, 'address': a, 'original': o.hex(), 'patched': p.hex()}
                for label, (a, o, _), p in zip(['init', 'override', 'blend', 'scratch'], records, variants)]})
            header += array(f'Init{device:04X}', variant) + array(f'Scratch{device:04X}', patched_scratch)
            header += f'inline constexpr Patch {name}[] = {{\n'
            for i, (address, original, _) in enumerate(records):
                replacement = f'Init{device:04X}' if i == 0 else f'Scratch{device:04X}' if i == 3 else f'Patched{i}'
                header += f'    {{0x{address-cache.base:X}ULL, Original{i}, {replacement}, {len(original)}}},\n'
            header += '};\n'
        header += ('struct DevicePatchSet { UInt32 deviceID; const Patch* patches; };\n'
                   'inline constexpr DevicePatchSet DevicePatches[] = {\n')
        for device in supported_devices():
            header += f'    {{0x{device:04X}, Patches{device:04X}}},\n'
        header += ('};\ninline const Patch* findDevice(UInt32 deviceID) {\n'
                   '    for (const auto& e : DevicePatches) { if (e.deviceID == deviceID) { return e.patches; } }\n'
                   '    return nullptr;\n}\n')
        header += resolver.header(locations)
        submit, submit_code = functions[SUBMIT]
        submit_resolver = ResolverData(cache, WORK)
        submit_resolver.add(submit, submit_code, submit_code)
        header += 'namespace Submit {\n' + submit_resolver.header([]) + '}\n}\n'
        (ROOT / 'NootedRed/UserSequoiaSurfaceData.hpp').write_text(header)
        report = {'records': [{'address': a, 'original': o.hex(), 'patched': p.hex()} for a, o, p in records],
                  'device_records': device_records, 'targets': targets, 'submit': submit, 'cacheUUID': cache.uuid.hex()}
        (WORK / 'patch-data.json').write_text(json.dumps(report, indent=2)+'\n')
        print('Generated Sequoia SRD, LegacyBlend, Scratch and full submit method:', len(header), 'bytes')
    finally:
        cache.close()


if __name__ == '__main__':
    main()
