"""生成指定缓存的 VCN2 核显视频补丁；仅修改本机设备的 VA 分类。"""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from audit_video_driver import symbols
from metal_cache import CACHE, CacheReader, array, macho
from vcn_capabilities import devices
from cache_resolver_data import ResolverData

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"
GVA_CHOICE_ADDRESS = 0x7FFA0C59BBAC
GVA_CHOICE_SIZE = 1136
GVA_CHOICE_SHA256 = "b7c725552045a9db206ab2eed4edda7ea3d27a29de2feee782204c362764a33d"
HASHES = {
    "__ZN17VAAcceleratorInfo8identifyEjj": "d7c579bce55c5724ec241f7f9abece9d0f23b1c30178d2f539fc0cabf41dc0bd",
    "__ZN18VAAddrLibInterface4initEP11VAIOConnectP13sHardwareInfo":
        "b35bd96d6ca0b8bd9047af67fc3f0f77ef09242f684c65e17d4cee062af155ea",
    "__ZN4Addr2V27Gfx9Lib20HwlConvertChipFamilyEjj":
        "4a4a1d2337e580de8dcd851ec686179bc375639441ebf7020dcca69ea780f8fa",
    "__ZN4Addr3Lib6CreateEPK18_ADDR_CREATE_INPUTP19_ADDR_CREATE_OUTPUT":
        "b52b9103faa80681fff4d65758ac961b9a3263add45c7597ea7074e61e4fb685",
    "__ZN9VAFactory20createGraphicsEngineEP9VAContext":
        "0cca569382df5a722cd0a569536315566bc0351207ae7fe0cb1ae4cca67a1327",
    "__ZN9VAFactory14createImageBltEP9VAContextj":
        "c5261154448c0030e75bf1e0f83a5152cb060d05857e317591b2a99ec18ab928",
    "__ZN9VAFactory17createDeblockerVPEi": "c1ff92f8d8465dfbf165087549acb38a393c633ae8766bb668f93a36e9cf5f14",
    "__ZN9VAFactory17createDitheringVPEi": "9655251548842be724c1dcd05f22d7dcf114a8923350fd69cef9ac3e7ec35a76",
    "__ZN9VAFactory20createDeinterlacerVPEi": "eb331d24e2a37de70736a36082d565cf2a39b31e57eedb8e9faa47172598ab8d",
    "__ZN9VAFactory29createColourFormatConverterVPEi":
        "5eb36cfc6c78526c1a3d4613cf55cb8d0f4c3a5967ecac16755b11d5ecdbac71",
    "__ZN9VAFactory31createDynamicContrastEnhancerVPEi":
        "32d515a6ca3132d7fa258389d7bb109a3541508ee4814949e73fdd728f253dcd",
}


def compile_payload(source, label):
    asm, obj = WORK / f"{label}.s", WORK / f"{label}.o"
    asm.write_text(source)
    subprocess.run(["xcrun", "clang", "-c", str(asm), "-o", str(obj)], check=True, timeout=60)
    data = obj.read_bytes()
    _, sections, _ = macho(lambda a, n: data[a:a + n], 0)
    section = next(s for s in sections if s[0] == "__text")
    return data[section[3]:section[3] + section[2]]


def identify_payload(native, device_id, va_family):
    code = compile_payload(".text\n.globl _dump\n_dump:\n.byte " + ",".join(hex(v) for v in native), "identify-dump")
    assert code == native
    output = subprocess.run(["xcrun", "llvm-objdump", "-d", str(WORK / "identify-dump.o")],
                            capture_output=True, text=True, check=True, timeout=60).stdout
    result = ".text\n.globl _payload\n_payload:\n"
    for line in output.splitlines():
        m = re.match(r"\s*([0-9a-f]+): ((?:[0-9a-f]{2}[ \t]+)+)([^#]+)", line)
        if not m:
            continue
        offset = int(m[1], 16)
        raw = bytes.fromhex(m[2])
        instruction = m[3].strip()
        result += f"L{offset:x}:\n"
        if offset in (0x24, 0x48, 0x5A):
            # 唯一外部调用与两处 RIP 数据访问使用原地址差，不产生外部重定位。
            prefix, displacement_offset = ((b"\x48\x8d\x05", 3) if offset == 0x24 else
                                           (b"\xe8", 1) if offset == 0x48 else (b"\x0f\xb7\x05", 3))
            target = offset + len(raw) + struct.unpack_from("<i", raw, displacement_offset)[0]
            result += ".byte " + ",".join(hex(v) for v in prefix) + "\n"
            result += f".long {target} - (. - _payload) - 4\n"
        elif instruction.startswith("j"):
            branch = re.match(r"(\w+)\s+0x([0-9a-f]+)", instruction)
            if not branch:
                raise ValueError("无法核实的识别分支")
            result += f"{branch[1]} L{branch[2]}\n"
        elif re.match(r"movl\s+\$0x[0-9a-f]+, %ebx", instruction):
            value = int(re.search(r"\$0x([0-9a-f]+)", instruction)[1], 16)
            result += f"pushq ${value}\npopq %rbx\n"
        else:
            result += ".byte " + ",".join(hex(v) for v in raw) + "\n"
        if offset == 0x5A:
            result += (f"cmpw ${device_id:#x}, %ax\njne Loriginal\npushq ${va_family}\npopq %rbx\n"
                       "jmp L51\nLoriginal:\n")
    result += ".org 448, 0x90\n"
    return compile_payload(result, f"identify-{device_id:04X}")


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    capabilities = devices()
    if (0x1638, 5) not in capabilities:
        raise ValueError("缺少既有 Cezanne 配置")
    cache = CacheReader()
    records = []
    full_functions = {}
    try:
        lines = (CACHE / "dyld_shared_cache_x86_64h.map").read_text().splitlines()
        image = "/System/Library/Extensions/AMDRadeonVADriver2.bundle/Contents/MacOS/AMDRadeonVADriver2"
        base = int(lines[lines.index(image) + 1].split()[1], 16)
        names, sections = symbols(cache.read, base)

        def function(name):
            address = names[name]
            end = min(a for a in names.values() if a > address)
            code = cache.read(address, end - address)
            if hashlib.sha256(code).hexdigest() != HASHES[name]:
                raise ValueError(f"原生函数未核实：{name}")
            full_functions[name] = (address, code)
            return address, code

        def add(name, address, original, patched):
            if len(original) != len(patched):
                raise ValueError("补丁长度不相等")
            records.append({"label": name, "address": address, "original": original.hex(), "patched": patched.hex()})

        gva_choice = cache.read(GVA_CHOICE_ADDRESS, GVA_CHOICE_SIZE)
        if hashlib.sha256(gva_choice).hexdigest() != GVA_CHOICE_SHA256:
            raise ValueError("AppleGVA 选择函数未核实，拒绝生成")
        assert gva_choice[0x287:0x28D] == bytes.fromhex("0f84ac010000")
        # 非专用 AMD 机型的原生回退只选择 Intel；无 Intel 时跳到已有的 AMD 插入路径。
        # 此前已检查至少存在一个可用 GPU，因此该出口必有 AMD，其他选择与显式请求不变。
        add("AppleGVA_AMDOnlyFallback", GVA_CHOICE_ADDRESS + 0x287,
            gva_choice[0x287:0x28D], bytes.fromhex("0f842a000000"))

        name = "__ZN17VAAcceleratorInfo8identifyEjj"
        address, original = function(name)
        identify_variants = {device: identify_payload(original, device, family) for device, family in capabilities}
        add(name, address, original, identify_variants[0x1638])
        for name, offset in [("__ZN9VAFactory20createGraphicsEngineEP9VAContext", 10),
                             ("__ZN9VAFactory14createImageBltEP9VAContextj", 11)]:
            address, code = function(name)
            assert code[offset:offset + 10] == bytes.fromhex("488b86600400008b400c")
            add(name, address + offset, code[offset:offset + 10], bytes.fromhex("b8020000009090909090"))
        for name in HASHES:
            if "VPEi" not in name:
                continue
            address, code = function(name)
            if bytes.fromhex("8d46ff83f80272") not in code:
                continue  # 原生没有 GFX9 实现的可选处理器保持原样。
            offset = code.index(bytes.fromhex("8d46ff83f80272"))
            assert code[offset + 7] < 0x80
            target = offset + 8 + code[offset + 7]
            patched = b"\xbe\x02\x00\x00\x00\xeb" + bytes([target - offset - 7]) + b"\x90"
            add(name, address + offset, code[offset:offset + 8], patched)
        name = "__ZN18VAAddrLibInterface4initEP11VAIOConnectP13sHardwareInfo"
        address, code = function(name)
        assert code[0x8B:0x92] == bytes.fromhex("4181fc8d000000")
        add(name, address + 0x8E, b"\x8d", b"\x8e")
        name = "__ZN4Addr3Lib6CreateEPK18_ADDR_CREATE_INPUTP19_ADDR_CREATE_OUTPUT"
        address, code = function(name)
        assert code[0xB5:0xBA] == bytes.fromhex("3d8d000000")
        add(name, address + 0xB6, b"\x8d", b"\x8e")
        name = "__ZN4Addr2V27Gfx9Lib20HwlConvertChipFamilyEjj"
        address, code = function(name)
        add(name, address, code, compile_payload((ROOT / "tools/video_addr_convert.s").read_text(), "addr-convert"))
        code_section = next(s for s in sections if s[0] == "__text")
        text = cache.read(code_section[1], code_section[2])
        for old, new in [(0x2220221, 0x2020201), (0x6660661, 0x6060601)]:
            cursor, count = 0, 0
            while (offset := text.find(struct.pack("<I", old), cursor)) >= 0:
                address = code_section[1] + offset
                parent = max((a, n) for n, a in names.items() if a <= address)
                if not any(s in parent[1] for s in ["HwlGetPreferredSurfaceSetting", "HwlComputeSurfaceInfoSanityCheck",
                                                   "IsValidDisplaySwizzleMode", "ValidateSwModeParams"]):
                    raise ValueError("swizzle mask 位于未核实的消费者")
                add(parent[1], address, struct.pack("<I", old), struct.pack("<I", new))
                count += 1
                cursor = offset + 1
            if count != 4:
                raise ValueError("swizzle mask 数量不匹配")
        records.sort(key=lambda r: r["address"])
        resolver = ResolverData(cache, WORK)
        functions = {"AppleGVA_AMDOnlyFallback": (GVA_CHOICE_ADDRESS, gva_choice), **full_functions}
        for record in records:
            label = record["label"]
            if label not in functions:
                address = names[label]
                end = min(a for a in names.values() if a > address)
                functions[label] = (address, cache.read(address, end-address))
        indices = {}
        for label, (address, code) in functions.items():
            if not any(r["label"] == label for r in records):
                continue
            patched = bytearray(code)
            for record in records:
                if record["label"] == label:
                    offset = record["address"]-address
                    patch = bytes.fromhex(record["patched"])
                    patched[offset:offset+len(patch)] = patch
            indices[label] = resolver.add(address, code, bytes(patched))
        locations = [(indices[r["label"]], r["address"]-functions[r["label"]][0]) for r in records]
        header = "// 由 tools/build_video_decode_patch.py 生成，不手工编辑。\n#pragma once\n#include <IOKit/IOTypes.h>\n#include <UserCacheResolver.hpp>\n"
        header += "namespace UserVideoDecodeData {\n" + array("CacheMagic", cache.magic) + array("CacheUUID", cache.uuid)
        header += f"inline constexpr UInt64 CacheBase = 0x{cache.base:X}ULL;\n"
        header += "struct Patch { UInt64 offset; const UInt8* original; const UInt8* patched; UInt32 size; };\n"
        for i, record in enumerate(records):
            address, size = record["address"], len(bytes.fromhex(record["original"]))
            if address // 4096 != (address + size - 1) // 4096:
                raise ValueError("补丁跨页")
            if i and records[i - 1]["address"] + len(bytes.fromhex(records[i - 1]["original"])) > address:
                raise ValueError("补丁重叠")
            header += array(f"Original{i}", bytes.fromhex(record["original"]))
            header += array(f"Patched{i}", bytes.fromhex(record["patched"]))
        header += "inline constexpr Patch Patches[] = {\n"
        for i, record in enumerate(records):
            header += f'    {{0x{record["address"] - cache.base:X}ULL, Original{i}, Patched{i}, sizeof(Original{i})}},\n'
        pages = len({r["address"] // 4096 for r in records})
        header += "};\n"
        identify_index = next(i for i, record in enumerate(records) if record['label'] == '__ZN17VAAcceleratorInfo8identifyEjj')
        device_records = []
        for device, _ in capabilities:
            variants = [dict(record) for record in records]
            variants[identify_index]['patched'] = identify_variants[device].hex()
            device_records.append({'device_id': device, 'records': variants})
            if device == 0x1638:
                continue
            suffix = f'{device:04X}'
            header += array(f'Identify{suffix}', identify_variants[device])
            header += f'inline constexpr Patch Patches{suffix}[] = {{\n'
            for i in range(len(records)):
                if i == identify_index:
                    header += (f'    {{Patches[{i}].offset, Original{i}, Identify{suffix}, sizeof(Original{i})}},\n')
                else:
                    header += f'    Patches[{i}],\n'
            header += '};\n'
        header += 'struct DevicePatchSet { UInt32 deviceID; const Patch* patches; };\n'
        header += 'inline constexpr DevicePatchSet DevicePatches[] = {\n'
        for device, _ in capabilities:
            name = 'Patches' if device == 0x1638 else f'Patches{device:04X}'
            header += f'    {{0x{device:04X}, {name}}},\n'
        header += '};\n'
        header += ('inline constexpr const Patch* findDevice(UInt32 deviceID) {\n'
                   '    for (const auto& entry : DevicePatches) { if (entry.deviceID == deviceID) { return entry.patches; } }\n'
                   '    return nullptr;\n}\n')
        header += resolver.header(locations)
        header += f"inline constexpr UInt32 MaxPatchSize = 448;\ninline constexpr UInt32 PageCount = {pages};\n}}\n"
        (ROOT / "NootedRed/UserVideoDecodeData.hpp").write_text(header)
        (WORK / "user-patches.json").write_text(json.dumps({"records": records, "pages": pages,
                                                           "device_records": device_records,
                                                           "cacheUUID": cache.uuid.hex()}, indent=2) + "\n")
        print("Generated video patches:", len(records), "pages:", pages, "VCN2 devices:", len(capabilities))
    finally:
        cache.close()


if __name__ == "__main__":
    main()
