"""生成经过版本与机器码核对的 Metal 修复数据，不写系统缓存。"""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
from metal_cache import CacheReader, macho, array
from build_compute_scratch_patch import generate as generate_scratch

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/user-surface-sync"
FUNCTIONS = {
    "init": ("__Z29amdMtl_HWL_InitDeviceSettingsP18AMD_DeviceSettingsP18AMDCommonHwInfoRec",
             "886322b1d50af710d30ed4354c9b3000fee462c86e7c6ac0300ba88c4b3321e4"),
    "override": ("__Z33amdMtl_HWL_OverrideDeviceSettingsP18AMD_DeviceSettingsP18AMDCommonHwInfoRec",
                 "8944785f90985578b38c777def41df74dd6028f0eac779cdf396a17dc104cc8b"),
}


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
                "<i", originals["init"]["address"] + 180 - item["address"] - 5) + b"\x90" * 5
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
        scratch_record = generate_scratch(cache)
        scratch_records = [dict(r) for r in legacy_records] + [scratch_record]
        pages = {r["address"] & ~4095 for r in scratch_records}
        for r in scratch_records:
            if r["address"] // 4096 != (r["address"] + len(bytes.fromhex(r["original"])) - 1) // 4096:
                raise ValueError("单段跨页")
        header = "// 由 tools/build_surface_sync_patch.py 生成，不手工编辑。\n#pragma once\n#include <IOKit/IOTypes.h>\n"
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
        header += array("ScratchPatched", bytes.fromhex(scratch_record["patched"]))
        header += "inline constexpr Patch ComputeScratchPatches[] = {\n"
        header += ",\n".join(f"    LegacyBlendPatches[{i}]" for i in range(len(legacy_records))) + ",\n"
        header += (f"    {{0x{scratch_record['address']-cache.base:X}ULL, ScratchOriginal, ScratchPatched, "
                   "sizeof(ScratchOriginal)}\n};\n")
        header += f"inline constexpr UInt32 MaxPatchSize = 452;\ninline constexpr UInt32 PageCount = {len(pages)};\n}}\n"
        (WORK / "UserSurfaceSyncData.hpp").write_text(header)
        (ROOT / "NootedRed/UserSurfaceSyncData.hpp").write_text(header)
        (WORK / "patch-data.json").write_text(json.dumps({"originals": originals, "records": records,
                                                         "srd_shared_records": candidate_records,
                                                         "legacy_blend_records": legacy_records,
                                                         "withdrawn_guards": withdrawn_guards,
                                                         "compute_scratch_records": scratch_records,
                                                         "cache_base": cache.base}, indent=2) + "\n")
        print(f"Generated {len(records)} baseline / {len(legacy_records)} blend / "
              f"{len(scratch_records)} scratch patches; at most {len(pages)} pages")
    finally:
        cache.close()


if __name__ == "__main__":
    main()
