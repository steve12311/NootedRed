"""从已核验的 x86_64h 缓存生成 VideoToolbox 解码候选数据。"""
import argparse
import bisect
import hashlib
from pathlib import Path
import struct

from audit_video_driver import symbols
from build_cache_section_targets import sections
from build_video_decode_patch import compile_payload
from cache_resolver_data import ResolverData
from metal_cache import CACHE, CacheReader, array
from vt_decoder_selection import VT, function_starts

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vcn"
REFERENCES = {
    23: "4c1441fd8a543dc2863d49110b946a25",
    24: "0faee1e17a093a16922ed6e1663c715f",
    25: "5a3da575f02e33dc9b14f663050011e6",
}
CLASSIFIER_SIZE = 0xF0
NATIVE_FIELDS = {
    "Once": 0x19, "Init": 0x20, "DispatchOnce": 0x25, "Lock": 0x2C,
    "Enter": 0x31, "CopyDevice": 0x39, "IsExternal": 0x43, "ReadBool": 0x4E,
    "Release": 0x5B, "Leave": 0x67, "IsSlotted": 0xBB,
}
NATIVE_REFS = (
    0x19, 0x20, 0x25, 0x2C, 0x31, 0x39, 0x43, 0x4E, 0x5B, 0x62, 0x67,
    0x91, 0x98, 0x9D, 0xA4, 0xA9, 0xB1, 0xBB, 0xC6, 0xD3, 0xDA, 0xDF,
)
NATIVE_SHA256 = "8191281c929caa9e4efcb80b25942f0fda458c74f6d66f8332b6a524cfcd4325"
DECODER_CALLERS_MODERN = (
    "_VTCopyVideoDecoderList",
    "_VTRegisterVideoDecoderBundleDirectory",
    "_VTCopyVideoDecoderExtensionProperties",
)
CF = "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation"


def call(site, target):
    return b"\xe8" + struct.pack("<i", target - (site + 5))


def classifier_targets(cache, resolver, names, layout):
    address = names["_VTIsMetalDeviceExternal"]
    slotted = names["_VTIsMetalDeviceSlotted"]
    if slotted != address + 0x78 or min(a for a in names.values() if a > slotted) != address + CLASSIFIER_SIZE:
        raise ValueError("VideoToolbox 分类入口布局不符")
    native = cache.read(address, CLASSIFIER_SIZE)
    normalized = bytearray(native)
    for offset in NATIVE_REFS:
        normalized[offset:offset + 4] = bytes(4)
    if hashlib.sha256(normalized).hexdigest() != NATIVE_SHA256:
        raise ValueError("VideoToolbox 分类函数 ABI 与参考不符")

    targets = {name: address + off + 4 + struct.unpack_from("<i", native, off)[0]
               for name, off in NATIVE_FIELDS.items()}
    targets["CopyRegistryValue"] = names["_VTCopyIORegistryValueForKeyFromDeviceRegistryID"]

    _, _, start, size, *_ = next(s for s in layout if s[1] == "__cfstring")
    codec_keys = []
    strings = cache.read(start, size)
    for offset in range(0, size, 32):
        _, flags, _, length = struct.unpack_from("<QQQQ", strings, offset)
        if flags == 0x7C8 and length == len(b"IOGVACodec"):
            pointer = resolver.pointer(start + offset + 16)
            if cache.read(pointer, length + 1) == b"IOGVACodec\0":
                codec_keys.append(start + offset)
    if len(codec_keys) != 1:
        raise ValueError("IOGVACodec 常量不唯一")
    targets["CodecKey"] = codec_keys[0]

    cf_base = next(s for p, ranges in resolver.catalog if p == CF for n, s, _ in ranges if n == "__TEXT")
    cf_names, _ = symbols(cache.read, cf_base)
    _, _, start, size, *_ = next(s for s in layout if s[1] == "__stubs")
    stubs = cache.read(start, size)
    wanted = {cf_names["_CFStringCreateWithCString"]: "CreateString", cf_names["_CFEqual"]: "Equal"}
    found = {key: [] for key in wanted}
    for offset in range(0, size - 5, 6):
        if stubs[offset:offset + 2] != b"\xff\x25":
            raise ValueError("VideoToolbox 调用桩格式不符")
        slot = start + offset + 6 + struct.unpack_from("<i", stubs, offset + 2)[0]
        destination = resolver.pointer(slot)
        if destination in found:
            found[destination].append(start + offset)
    for destination, key in wanted.items():
        if len(found[destination]) != 1:
            raise ValueError(f"VideoToolbox 调用桩不唯一：{key}")
        targets[key] = found[destination][0]
    return address, native, targets


def decoder_contexts(cache, base, names, slotted, callers):
    starts = function_starts(cache, base)
    for symbol in callers:
        start = names[symbol]
        end = min(a for a in names.values() if a > start)
        if not 0 < end - start <= 65536:
            raise ValueError(f"解码候选调用范围异常：{symbol}")
        scope = cache.read(start, end - start)
        sites = [start + i for i in range(len(scope) - 4)
                 if scope[i] == 0xE8 and scope[i:i + 5] == call(start + i, slotted)]
        if len(sites) != 1:
            raise ValueError(f"解码分类调用不唯一：{symbol}")
        site = sites[0]
        index = bisect.bisect_right(starts, site) - 1
        if index < 0 or index + 1 >= len(starts) or starts[index] < start or site + 5 > starts[index + 1]:
            raise ValueError(f"解码分类函数边界不匹配：{symbol}")
        address = starts[index]
        yield site, address, cache.read(address, starts[index + 1] - address)


def generate(cache, major):
    resolver = ResolverData(cache, WORK)
    base = next(s for p, ranges in resolver.catalog if p == VT for n, s, _ in ranges if n == "__TEXT")
    names, _ = symbols(cache.read, base)
    address, native, targets = classifier_targets(cache, resolver, names, sections(cache, base))
    source = "".join(f".set {key}, {target - address}\n" for key, target in targets.items())
    source += (ROOT / "tools/vt_rdd_candidates.s").read_text()
    patched = compile_payload(source, "vt-rdd-candidates")
    if len(patched) != len(native):
        raise ValueError("VideoToolbox 分类补丁长度不符")

    resolver.add(address, native, patched)
    patches = [(address, native, patched)]
    locations = [(0, 0)]
    callers = ("_VTRegisterVideoDecoderWithInfo",) if major == 23 else DECODER_CALLERS_MODERN
    for site, begin, original in decoder_contexts(cache, base, names, address + 0x78, callers):
        offset = site - begin
        replacement = call(site, address + 0x81)
        function = resolver.add(begin, original, original[:offset] + replacement + original[offset + 5:])
        if (offset + 1, 0, address + 0x78 - base, 0) not in resolver.functions[function][3]:
            raise ValueError("解码分类调用不是已核验的直接调用指令")
        locations.append((function, offset))
        patches.append((site, original[offset:offset + 5], replacement))

    out = ""
    for index, (_, original, patched) in enumerate(patches):
        out += array(f"Original{index}", original) + array(f"Patched{index}", patched)
    out += "inline constexpr UserVideoDecodeData::Patch Patches[] = {\n"
    for index, (site, _, _) in enumerate(patches):
        out += f"    {{0x{site - cache.base:X}ULL, Original{index}, Patched{index}, sizeof(Original{index})}},\n"
    return out + "};\n" + resolver.header(locations) + resolver.section_header() + (
        "struct Profile {\n"
        "    static constexpr auto& Sites = Patches;\n"
        "    static constexpr auto& Images = ResolverImages;\n"
        "    static constexpr auto& Functions = ResolverFunctions;\n"
        "    static constexpr auto& Locations = ResolverLocations;\n"
        "    static constexpr auto& Targets = SectionTargets;\n"
        "};\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sonoma-cache", type=Path, required=True)
    parser.add_argument("--sequoia-cache", type=Path, required=True)
    parser.add_argument("--tahoe-cache", type=Path, default=CACHE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    out = "// 由 tools/build_vt_rdd_candidates.py 生成。\n#pragma once\n#include <UserVideoDecodeData.hpp>\n"
    for major, label, directory in ((23, "Sonoma", args.sonoma_cache), (24, "Sequoia", args.sequoia_cache),
                                     (25, "Tahoe", args.tahoe_cache)):
        cache = CacheReader(directory, REFERENCES[major])
        try:
            out += f"\nnamespace UserVTRDDCandidateData::{label} {{\n" + generate(cache, major) + "}\n"
        finally:
            cache.close()
    path = ROOT / "NootedRed/UserVTRDDCandidateData.hpp"
    if args.check:
        if path.read_text() != out:
            raise ValueError("VideoToolbox 分类补丁生成数据过期")
    else:
        path.write_text(out)


if __name__ == "__main__":
    main()
