"""生成经过版本与机器码核对的 Metal 修复数据，不写系统缓存。"""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
from metal_cache import CacheReader, macho, array, IMAGE
from build_compute_scratch_patch import generate as generate_scratch, supported_devices
from cache_resolver_data import ResolverData, containing_function

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/user-surface-sync"
FUNCTIONS = {
    "init": ("__Z29amdMtl_HWL_InitDeviceSettingsP18AMD_DeviceSettingsP18AMDCommonHwInfoRec",
             "886322b1d50af710d30ed4354c9b3000fee462c86e7c6ac0300ba88c4b3321e4"),
    "override": ("__Z33amdMtl_HWL_OverrideDeviceSettingsP18AMD_DeviceSettingsP18AMDCommonHwInfoRec",
                 "8944785f90985578b38c777def41df74dd6028f0eac779cdf396a17dc104cc8b"),
}


def for_device(records, device_id):
    # 完整 32 位比较只作用于本机核显，保留同进程中的独显原生初始化。
    guard = bytes.fromhex("817e0c38160000")
    payload = bytes.fromhex(records[0]["patched"])
    if payload.count(guard) != 1:
        raise ValueError("初始化设备比较指令未核实")
    result = [dict(record) for record in records]
    result[0]["patched"] = payload.replace(guard, guard[:3] + struct.pack("<I", device_id)).hex()
    return result


def relocation_fields(cache, code, address, targets, label):
    # 渲染专属目标编号保留；指令／数据分界与 rel32 解码复用公共生成器。
    resolver = ResolverData(cache, WORK)
    fields = []
    for offset, image, rva, indirect in resolver.references(code, address, label):
        target = resolver.images[image][1]+rva
        if indirect or target not in targets:
            raise ValueError(f'未核实的渲染重定位目标：{target:#x}')
        fields.append((offset, targets.index(target)))
    if sorted(kind for _, kind in fields) != [0, 1, 2]:
        raise ValueError('初始化外部引用不完整')
    return fields


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    cache = CacheReader()
    try:
        originals = {}
        for label, (name, digest) in FUNCTIONS.items():
            address, code = cache.function(name)
            if hashlib.sha256(code).hexdigest() != digest:
                raise ValueError(f"{label} 未核实，拒绝生成")
            originals[label] = {"address": address, "code": code.hex()}
        if cache.read(0x7FFB10FBFDE8, 4) != struct.pack("<f", 3.0):
            raise ValueError("原生常量不匹配")
        obj = WORK / "init.o"
        subprocess.run(["clang", "-c", str(ROOT / "tools/surface_sync_init.s"), "-o", str(obj)],
                       check=True, timeout=60)
        data = obj.read_bytes()
        _, sections, _ = macho(lambda a, n: data[a:a+n], 0)
        text = next(s for s in sections if s[0] == "__text")
        payload = data[text[3]:text[3]+text[2]]
        if len(payload) != 215:
            raise ValueError("初始化补丁长度错误")
        records = []
        for label in FUNCTIONS:
            item = originals[label]
            patched = payload if label == "init" else b"\xe9" + struct.pack(
                "<i", originals["init"]["address"] + 166 - item["address"] - 5) + b"\x90" * 5
            if len(patched) != len(bytes.fromhex(item["code"])):
                raise ValueError("补丁越界")
            records.append({"label": label, "address": item["address"],
                            "original": item["code"], "patched": patched.hex()})
        # 单独生成 shared SRD 策略，保留只禁用延迟同步的兼容模式。
        candidate_obj = WORK / "srd-shared-init.o"
        subprocess.run(["clang", "-c", str(ROOT / "tools/surface_sync_srd_shared_init.s"),
                        "-o", str(candidate_obj)], check=True, timeout=60)
        candidate_data = candidate_obj.read_bytes()
        _, candidate_sections, _ = macho(lambda a, n: candidate_data[a:a+n], 0)
        candidate_text = next(s for s in candidate_sections if s[0] == "__text")
        candidate_payload = candidate_data[candidate_text[3]:candidate_text[3]+candidate_text[2]]
        if len(candidate_payload) != 215:
            raise ValueError("共享 SRD 的补丁长度错误")
        candidate_records = [dict(r) for r in records]
        candidate_records[0]["patched"] = candidate_payload.hex()
        device_records = [{"device_id": device, "records": for_device(records, device),
                           "srd_shared_records": for_device(candidate_records, device)}
                          for device in supported_devices()]
        ctor_address, ctor_code = cache.function(
            "-[GFX9_MtlRenderPipelineState initWithDevice:pipelineStateDescriptor:vertexVariant:fragmentVariant:]")
        if hashlib.sha256(ctor_code).hexdigest() != "9d78fd28bb509a95ce68fdb879d507388bf1625f54439da53622aa1097ee339b":
            raise ValueError("pipeline 构造函数未核实")
        gate_address = 0x7ffb10c77708
        gate_code = cache.read(gate_address, 2)
        if gate_code != bytes.fromhex("7428"):
            raise ValueError("RB+ 能力分支未核实")
        legacy_records = [dict(r) for r in candidate_records]
        legacy_records.append({"label": "blend-capability-gate", "address": gate_address,
                               "original": gate_code.hex(), "patched": "9090"})
        # 仅保留旧实验的原始字节检查；不生成已撤回的 VT 替换代码。
        withdrawn_guards = []
        for address, expected in [(0x7FF81585FD97, "7428"), (0x7FF815861D17, "0f8482040000")]:
            original = cache.read(address, len(bytes.fromhex(expected)))
            if original != bytes.fromhex(expected):
                raise ValueError("VideoToolbox 原始选择分支不匹配")
            withdrawn_guards.append({"address": address, "original": original.hex()})
        device_scratch_records = [
            {"device_id": device, "records": [*legacy_records, generate_scratch(cache, device)]}
            for device in supported_devices()
        ]
        scratch_record = device_scratch_records[0]["records"][-1]
        scratch_records = device_scratch_records[0]["records"]
        pages = {r["address"] & ~4095 for r in scratch_records}
        for r in scratch_records:
            if r["address"] // 4096 != (r["address"] + len(bytes.fromhex(r["original"])) - 1) // 4096:
                raise ValueError("单段跨页")
        header = "// 由 tools/build_surface_sync_patch.py 生成，不手工编辑。\n#pragma once\n#include <IOKit/IOTypes.h>\n#include <UserCacheResolver.hpp>\n"
        header += "namespace UserSurfaceSyncData {\n" + array("CacheMagic", cache.magic) + array("CacheUUID", cache.uuid)
        header += f"inline constexpr UInt64 CacheBase = 0x{cache.base:X}ULL;\n"
        header += "struct Patch { UInt64 offset; const UInt8* original; const UInt8* patched; UInt32 size; };\n"
        for i, r in enumerate(records):
            header += array(f"Original{i}", bytes.fromhex(r["original"])) + array(f"Patched{i}", bytes.fromhex(r["patched"]))
            header += array(f"SrdSharedPatched{i}", bytes.fromhex(candidate_records[i]["patched"]))

        header += "inline constexpr Patch Patches[] = {\n" + ",\n".join(
            f"    {{0x{r['address']-cache.base:X}ULL, Original{i}, Patched{i}, sizeof(Original{i})}}"
            for i, r in enumerate(records)) + "\n};\n"
        header += "inline constexpr Patch SrdSharedPatches[] = {\n" + ",\n".join(
            f"    {{0x{r['address']-cache.base:X}ULL, Original{i}, SrdSharedPatched{i}, sizeof(Original{i})}}"
            for i, r in enumerate(records)) + "\n};\n"
        header += array("BlendGateOriginal", gate_code) + array("BlendGatePatched", bytes.fromhex("9090"))
        header += "inline constexpr Patch LegacyBlendPatches[] = {\n" + ",\n".join(
            f"    {{0x{r['address']-cache.base:X}ULL, " +
            (f"Original{i}, SrdSharedPatched{i}, sizeof(Original{i})" if i < 2 else
             "BlendGateOriginal, BlendGatePatched, sizeof(BlendGateOriginal)") + "}"
            for i, r in enumerate(legacy_records)) + "\n};\n"
        header += "struct Guard { UInt64 offset; const UInt8* original; UInt32 size; };\n"
        for i, r in enumerate(withdrawn_guards):
            header += array(f"WithdrawnOriginal{i}", bytes.fromhex(r["original"]))
        header += "inline constexpr Guard WithdrawnTransferGuards[] = {\n" + ",\n".join(
            f"    {{0x{r['address']-cache.base:X}ULL, WithdrawnOriginal{i}, sizeof(WithdrawnOriginal{i})}}"
            for i, r in enumerate(withdrawn_guards)) + "\n};\n"
        header += array("ScratchOriginal", bytes.fromhex(scratch_record["original"]))
        # 各设备共享原始代码、覆盖跳转与混合分支；仅初始化比较立即数不同。
        for entry in device_records[1:]:
            suffix = f"{entry['device_id']:04X}"
            for mode, key in [("Immediate", "records"), ("Shared", "srd_shared_records")]:
                header += array(f"{mode}Init{suffix}", bytes.fromhex(entry[key][0]["patched"]))
                header += f"inline constexpr Patch {mode}Patches{suffix}[] = {{\n"
                header += (f"    {{Patches[0].offset, Original0, {mode}Init{suffix}, sizeof(Original0)}},\n"
                           f"    Patches[1]\n}};\n")
            header += f"inline constexpr Patch BlendPatches{suffix}[] = {{\n"
            header += (f"    SharedPatches{suffix}[0], SharedPatches{suffix}[1], LegacyBlendPatches[2]\n}};\n")
        for entry in device_scratch_records:
            device = entry["device_id"]
            suffix = f"{device:04X}"
            patched_name = "ScratchPatched" if device == 0x1638 else f"ScratchPatched{suffix}"
            patch_name = "ComputeScratchPatches" if device == 0x1638 else f"ComputeScratchPatches{suffix}"
            blend_name = "LegacyBlendPatches" if device == 0x1638 else f"BlendPatches{suffix}"
            record = entry["records"][-1]
            header += array(patched_name, bytes.fromhex(record["patched"]))
            header += f"inline constexpr Patch {patch_name}[] = {{\n"
            header += ",\n".join(f"    {blend_name}[{i}]" for i in range(len(legacy_records))) + ",\n"
            header += (f"    {{0x{record['address']-cache.base:X}ULL, ScratchOriginal, {patched_name}, "
                       "sizeof(ScratchOriginal)}\n};\n")
        header += ("struct DevicePatchSet { UInt32 deviceID; const Patch* immediate; const Patch* shared; "
                   "const Patch* blend; const Patch* scratch; };\n")
        header += "inline constexpr DevicePatchSet DevicePatches[] = {\n"
        header += "    {0x1638, Patches, SrdSharedPatches, LegacyBlendPatches, ComputeScratchPatches},\n"
        header += ",\n".join(f"    {{0x{e['device_id']:04X}, ImmediatePatches{e['device_id']:04X}, "
                             f"SharedPatches{e['device_id']:04X}, BlendPatches{e['device_id']:04X}, "
                             f"ComputeScratchPatches{e['device_id']:04X}}}"
                             for e in device_records[1:]) + "\n};\n"
        header += ("inline constexpr const DevicePatchSet* findDevice(UInt32 deviceID) {\n"
                   "    for (const auto& entry : DevicePatches) { if (entry.deviceID == deviceID) { return &entry; } }\n"
                   "    return nullptr;\n}\n")
        # 动态缓存解析只接受相同实现的完整函数；UUID 不参与兼容判定。
        targets = [0x7FFB10FBFDE8, 0x7FFB10C5422B, 0x7FFB10C545C6]
        init_address = originals['init']['address']
        variants = [('OriginalInitLinks', bytes.fromhex(records[0]['original'])),
                    ('ImmediateInitLinks', payload), ('SharedInitLinks', candidate_payload)]
        header += 'struct RelativeField { UInt32 offset; UInt32 target; };\n'
        for name, code in variants:
            fields = relocation_fields(cache, code, init_address, targets, name)
            header += f'inline constexpr RelativeField {name}[] = {{\n'
            header += ',\n'.join(f'    {{{offset}, {kind}}}' for offset, kind in fields) + '\n};\n'
        for name, address, size in [('NativeOverride', targets[1], 923), ('NativeDump', targets[2], 6)]:
            native = cache.read(address, size)
            if name == 'NativeOverride' and hashlib.sha256(native).hexdigest() != '9f4d151c607e74ed7710e81d437013fef73afe83bbbbd94179d2d429ca611ea6':
                raise ValueError('原生配置函数未核实')
            if name == 'NativeDump' and native.hex() != '554889e55dc3':
                raise ValueError('原生调试函数未核实')
            header += array(name, native)
        header += array('BlendConstructor', ctor_code)
        header += f'inline constexpr UInt32 BlendGateInConstructor = {gate_address - ctor_address};\n'
        header += f'inline constexpr UInt32 OverrideEntryInInit = 166;\n'
        header += f'inline constexpr char DriverPath[] = "{IMAGE}";\n'
        for name, address in [('InitRVA', init_address), ('OverrideRVA', originals['override']['address']),
                              ('NativeOverrideRVA', targets[1]), ('ConstructorRVA', ctor_address),
                              ('ScratchRVA', scratch_record['address'])]:
            header += f'inline constexpr UInt64 {name} = 0x{address-cache.image_base:X}ULL;\n'
        scratch_resolver = ResolverData(cache, WORK / 'scratch')
        scratch_resolver.add(scratch_record['address'], bytes.fromhex(scratch_record['original']),
                             bytes.fromhex(scratch_record['patched']))
        # 撤回策略的 VT 跳转仍需按完整原生函数核验，不能沿用参考缓存的固定偏移。
        _, vt_base = scratch_resolver.owner(withdrawn_guards[0]['address'])
        vt_address, vt_code = containing_function(cache, vt_base, withdrawn_guards[0]['address'])
        if not vt_address <= withdrawn_guards[1]['address'] < vt_address + len(vt_code):
            raise ValueError('撤回跳转的原生函数范围变化')
        scratch_resolver.add(vt_address, vt_code, vt_code)
        header += 'namespace ScratchResolverData {\n' + scratch_resolver.header([(0, 0)]) + '}\n'
        header += f"inline constexpr UInt32 MaxPatchSize = 452;\ninline constexpr UInt32 PageCount = {len(pages)};\n}}\n"
        (WORK / "UserSurfaceSyncData.hpp").write_text(header)
        (ROOT / "NootedRed/UserSurfaceSyncData.hpp").write_text(header)
        (WORK / "patch-data.json").write_text(json.dumps({"originals": originals, "records": records,
                                                         "srd_shared_records": candidate_records,
                                                         "device_records": device_records,
                                                         "legacy_blend_records": legacy_records,
                                                         "withdrawn_guards": withdrawn_guards,
                                                         "compute_scratch_records": scratch_records,
                                                         "device_compute_scratch_records": device_scratch_records,
                                                         "cache_base": cache.base}, indent=2) + "\n")
        print(f"Generated render patches for {len(device_records)} NRed PCI IDs / "
              f"{len(device_scratch_records)} device-specific scratch patches; at most {len(pages)} pages")
    finally:
        cache.close()


if __name__ == "__main__":
    main()
