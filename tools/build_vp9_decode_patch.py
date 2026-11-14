"""VP9 plan, phase V3: build the VP9 backend patch for AMDRadeonVADriver2 (behind -NRedVP9).

Compiles tools/vp9_backend/VP9Backend.cpp, links it into the AV1 decode processor's code (never run on VCN 2),
adds the four hooks that route kAVDPictureDecodeVP9 to it and writes NootedRed/UserVP9DecodeData.hpp in the format of
the other user patches (full-function matching and rel32 rebuilding at run time, see UserCacheResolver.hpp).
Design and driver offsets: docs/vp9-v0-findings.md, "V3".

Usage: python3 tools/build_vp9_decode_patch.py [--uuid <cache uuid>] [--check]
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

from audit_video_driver import symbols
from cache_resolver_data import ResolverData
from metal_cache import CacheReader, array

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vp9/backend"
IMAGE = "/System/Library/Extensions/AMDRadeonVADriver2.bundle/Contents/MacOS/AMDRadeonVADriver2"
DEFAULT_UUID = "3137475689b434e8a34f56c5d57216a1"
MAX_SITE = 448
CFLAGS = ["-std=c++17", "-target", "x86_64-apple-macos13", "-Oz", "-ffreestanding", "-fno-exceptions", "-fno-rtti",
          "-fno-stack-protector", "-fno-asynchronous-unwind-tables", "-fvisibility=hidden", "-Wall", "-Wextra",
          "-Werror", f"-I{ROOT / 'NootedRed'}"]

AV1 = "AvdVcnAv1DecDataProcessor"
# Driver symbols the backend calls, by the name it declares.
EXTERNALS = {
    "_nrProcessVcnCreateDecodeSession": "__ZN19DecodeDataProcessor29ProcessVcnCreateDecodeSessionEP23_uvd_command_build_info",
    "_nrPrepareBitStream": "__ZN19DecodeDataProcessor16PrepareBitStreamEP13_decrypt_info",
    "_nrGetSegFgBuffer": "__ZN9VADecoder14getSegFgBufferEv",
    "_nrGetIOSurfaceBuffer": "__ZN9VAContext18getIOSurfaceBufferEP11__IOSurfacePP15VAVendorTexturejPjb",
    "_nrSetRefCon": "__ZN9VAContext9setRefConEP14_CommandRefConP15VAVendorTexturePv",
    "_nrGetAVDFuncSubId": "__ZN13AvdCapability15getAVDFuncSubIdEj",
    "_nrGetAVDFuncOutputFormatInfo": "__ZN13AvdCapability26getAVDFuncOutputFormatInfoEjPjP16AVD_BufferFormat",
}
SETUP_CORE_FUNCTIONS = "__ZN13AvdCapability24setupStaticCoreFunctionsEv"
INIT_CAPABILITY = "__ZN13AvdCapability20initDriverCapabilityEjj"
GET_BUFFER_REQUIREMENT = "__ZN12AvdVAContext20GetBufferRequirementEP31AVD_DriverBufferRequirementInfo"
GET_CONTEXT_PARAMETER = "__ZN14AvdVAInterface19GetContextParameterEPvP20AVD_ContextParamList"
DECODE_PICTURE = "__ZN12AvdVAContext13DecodePictureEP11AVD_CmdInfoP14_CommandRefCon"
GET_FUNCTION_TYPE = "__ZN12AvdVAContext17GetVAFunctionTypeEP16AVD_FunctionInfo"
CREATE_SESSION = EXTERNALS["_nrProcessVcnCreateDecodeSession"]
CREATE_PROCESSOR = "__ZN9VAFactory25createDecodeDataProcessorE23DecodeDataProcessorTypeP9VADecoder"
# AV1 vtable entries (byte offset in the vtable) that become VP9 methods, and the backend function each jumps to.
SLOTS = {
    0x20: ("__ZN25AvdVcnAv1DecDataProcessor26ProcessCreateDecodeSessionEP23_uvd_command_build_info",
           "_vp9ProcessCreateDecodeSession"),
    0x50: ("__ZN25AvdVcnAv1DecDataProcessor16CalculateDPBSizeEv", "_vp9CalculateDPBSize"),
    0x58: ("__ZN25AvdVcnAv1DecDataProcessor20CalculateContextSizeEv", "_vp9CalculateContextSize"),
    0x68: ("__ZN25AvdVcnAv1DecDataProcessor19getDecodeTargetAreaEPvP8rect_int", "_vp9GetDecodeTargetArea"),
    0x70: ("__ZN25AvdVcnAv1DecDataProcessor13initHWContextEv", "_vp9InitHWContext"),
    0x78: ("__ZN25AvdVcnAv1DecDataProcessor18SetBitstreamFieldsEPvP15VAVendorTextureS2_P23_uvd_command_build_info",
           "_vp9SetBitstreamFields"),
    0x80: ("__ZN25AvdVcnAv1DecDataProcessor22SetDecodeMessageFieldsEPvP15VAVendorTextureP23_uvd_command_build_infojj",
           "_vp9SetDecodeMessageFields"),
}
# Entries that must keep pointing at the original AV1 code (they only call into DecodeDataProcessor or the vtable).
KEPT = {"ProcessDestroyDecodeSession", "SetupDRMCommand", "SetDrmPartialEncryption", "Process",
        "ProcessPictureDecodeParam"}


# ---- Mach-O object reader and linker -------------------------------------------------------------------------------

def read_object(path):
    data = path.read_bytes()
    magic, _, _, _, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", data)
    if magic != 0xFEEDFACF:
        raise ValueError("backend object is not Mach-O 64")
    cursor, sections, symtab = 32, [], None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, cursor)
        if cmd == 0x19:
            nsects = struct.unpack_from("<I", data, cursor + 64)[0]
            for i in range(nsects):
                s = cursor + 72 + i * 80
                sect, seg = data[s:s + 16].rstrip(b"\0").decode(), data[s + 16:s + 32].rstrip(b"\0").decode()
                addr, sz, off, align, reloff, nreloc, flags = struct.unpack_from("<QQIIIII", data, s + 32)
                relocs = [struct.unpack_from("<iI", data, reloff + 8 * r) for r in range(nreloc)]
                sections.append({"name": f"{seg},{sect}", "addr": addr, "size": sz, "align": 1 << align,
                                 "bytes": data[off:off + sz] if flags & 0xFF != 1 else bytes(sz), "relocs": relocs})
        elif cmd == 0x2:
            symtab = struct.unpack_from("<IIII", data, cursor + 8)
        cursor += size
    symoff, nsyms, stroff, _ = symtab
    syms = []
    for i in range(nsyms):
        strx, ntype, nsect, _, value = struct.unpack_from("<IBBhQ", data, symoff + 16 * i)
        name = data[stroff + strx:data.index(b"\0", stroff + strx)].decode()
        syms.append((name, ntype, nsect, value))
    return sections, syms


def link(sections, syms, placement, externals):
    """Places each section at placement[name] and applies BRANCH/SIGNED relocations.
    Returns {address: bytes} chunks, the defined symbols and the external rel32 fields (field address, target)."""
    new_base = {}
    for i, s in enumerate(sections, 1):
        if s["size"] == 0:
            continue
        if s["name"] not in placement:
            raise ValueError(f"backend section without a place: {s['name']}")
        new_base[i] = placement[s["name"]]
        if new_base[i] % s["align"]:
            raise ValueError(f"misaligned section {s['name']}")
    defined = {}
    for name, ntype, nsect, value in syms:
        if ntype & 0xE == 0xE:    # N_SECT
            defined[name] = value - sections[nsect - 1]["addr"] + new_base[nsect]
    out, external_fields = {}, []
    for i, s in enumerate(sections, 1):
        if s["size"] == 0:
            continue
        code = bytearray(s["bytes"])
        for address, info in s["relocs"]:
            if address < 0:
                raise ValueError("scattered relocation")
            symbolnum, pcrel, length, extern, kind = info & 0xFFFFFF, (info >> 24) & 1, (info >> 25) & 3, \
                (info >> 27) & 1, info >> 28
            if not pcrel or length != 2 or kind not in (1, 2):    # SIGNED, BRANCH
                raise ValueError(f"unsupported relocation kind {kind} in {s['name']}")
            field = new_base[i] + address
            addend = struct.unpack_from("<i", code, address)[0]
            if extern:
                name = syms[symbolnum][0]
                target = defined.get(name, externals.get(name))
                if target is None:
                    raise ValueError(f"unresolved backend symbol {name}")
                value = target + addend - (field + 4)
                if name in externals:
                    external_fields.append((field, target))
            else:    # section-relative: the field holds object-address displacement
                old_target = sections[i - 1]["addr"] + address + 4 + addend
                owner = next(j for j, t in enumerate(sections, 1) if t["addr"] <= old_target < t["addr"] + t["size"])
                target = old_target - sections[owner - 1]["addr"] + new_base[owner]
                value = target - (field + 4)
            struct.pack_into("<i", code, address, value)
        out[new_base[i]] = bytes(code)
    return out, defined, external_fields


def rel32(source, size, target):
    return struct.pack("<i", target - (source + size))


# ---- Generator -----------------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uuid", default=DEFAULT_UUID, help="UUID of the reference shared cache")
    parser.add_argument("--check", action="store_true", help="verify the checked-in header instead of writing it")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)

    obj = WORK / "VP9Backend.o"
    subprocess.run(["xcrun", "clang++", *CFLAGS, "-c", str(ROOT / "tools/vp9_backend/VP9Backend.cpp"), "-o", str(obj)],
                   check=True, timeout=120)
    sections, syms = read_object(obj)

    cache = CacheReader(uuid=args.uuid)
    try:
        lines = (cache.directory / "dyld_shared_cache_x86_64h.map").read_text().splitlines()
        base = int(lines[lines.index(IMAGE) + 1].split()[1], 16)
        names, _ = symbols(cache.read, base)
        ordered = sorted(set(names.values()))

        def extent(name):
            address = names[name]
            return address, ordered[ordered.index(address) + 1] - address

        # AV1 region: from ProcessCreateDecodeSession (after the constructors and destructors, kept) to the end of
        # getDecodeTargetArea. Nothing outside it branches in (checked from the vtable here and by docs/V3).
        start = names[SLOTS[0x20][0]]
        last, last_size = extent(SLOTS[0x68][0])
        end = last + last_size
        region = cache.read(start, end - start)
        members = sorted((a, n) for n, a in names.items() if start <= a < end)
        if not all(f"{len(AV1)}{AV1}" in n for _, n in members):
            raise ValueError("unexpected symbol inside the AV1 region")

        # vtable: every entry inside the region is a slot we replace or one we keep.
        vtable = names["__ZTV25AvdVcnAv1DecDataProcessor"]
        for slot in range(0x10, 0x88, 8):
            raw = struct.unpack("<Q", cache.read(vtable + slot, 8))[0]
            target = (raw & 0xFFFFFFFFF) + cache.base
            owner = next((n for a, n in members if a == target), None)
            if start <= target < end:
                if slot in SLOTS:
                    if names[SLOTS[slot][0]] != target:
                        raise ValueError(f"vtable slot {slot:#x} does not point at {SLOTS[slot][0]}")
                elif not owner or not any(k in owner for k in KEPT):
                    raise ValueError(f"vtable slot {slot:#x} points into the region at an unknown method")

        # Layout: jumps at the replaced entries, code after SetDecodeMessageFields' jump, data after initHWContext's.
        message_fields = names[SLOTS[0x80][0]]
        init_context = names[SLOTS[0x70][0]]
        code_start = (message_fields + 5 + 15) & ~15
        data_start = (init_context + 5 + 15) & ~15
        placement, cursor = {}, code_start
        for s in sections:
            if s["name"] == "__TEXT,__text":
                placement[s["name"]] = cursor
                cursor += s["size"]
        if cursor > init_context:
            raise ValueError(f"backend code too large: {cursor - code_start} bytes, room {init_context - code_start}")
        cursor = data_start
        for s in sections:
            if s["name"] != "__TEXT,__text" and s["size"]:
                cursor = (cursor + s["align"] - 1) & ~(s["align"] - 1)
                placement[s["name"]] = cursor
                cursor += s["size"]
        if cursor > last:
            raise ValueError(f"backend data too large: {cursor - data_start} bytes, room {last - data_start}")
        externals = {k: names[v] for k, v in EXTERNALS.items()}
        epilogue = names[DECODE_PICTURE] + 0x41
        if cache.read(epilogue, 3) != bytes.fromhex("4489e0"):    # mov eax, r12d
            raise ValueError("AvdVAContext::DecodePicture epilogue not found")
        externals["_nrDecodePictureEpilogue"] = epilogue
        # NV12 case of getAVDFuncOutputFormatInfo: *count = 1, formats[0] = {'NV12', 1}, return 0 (pops rbp).
        nv12 = names[EXTERNALS["_nrGetAVDFuncOutputFormatInfo"]] + 0x5D923 - 0x5D8FA
        if cache.read(nv12, 12) != bytes.fromhex("c70201000000c7013231564e"):
            raise ValueError("getAVDFuncOutputFormatInfo NV12 case not found")
        externals["_nrOutputFormatNV12"] = nv12
        chunks, defined, external_fields = link(sections, syms, placement, externals)

        patched = bytearray(region)
        for address, code in chunks.items():
            patched[address - start:address - start + len(code)] = code
        for slot, (method, function) in SLOTS.items():
            source = names[method]
            patched[source - start:source - start + 5] = b"\xe9" + rel32(source, 5, defined[function])
        state_size = struct.unpack_from("<I", chunks[placement["__TEXT,__const"]],
                                        defined["_vp9StateSize"] - placement["__TEXT,__const"])[0]

        # Hooks outside the region.
        hooks = []

        def hook(function, offset, original, replacement):
            address, size = extent(function)
            code = cache.read(address, size)
            if code[offset:offset + len(original)] != original:
                raise ValueError(f"unexpected bytes in {function} at {offset:#x}")
            # Several hooks in one function share one verified function (the resolver refuses overlaps).
            for i, (f, a, c, n, sites) in enumerate(hooks):
                if f == function:
                    n[offset:offset + len(replacement)] = replacement
                    sites.append((offset, len(original)))
                    return
            new = bytearray(code)
            new[offset:offset + len(replacement)] = replacement
            hooks.append((function, address, code, new, [(offset, len(original))]))

        # 1. kind 1 type 0x1000 (VP9) gets function type 6, the AV1 decoder (AV1 does not exist on VCN 2).
        hook(GET_FUNCTION_TYPE, 0x5DD8D - 0x5DC14, bytes.fromhex("483d00000001"), bytes.fromhex("483d00100000"))
        # 2. Create message: codec 0x13 (AV1) -> 0x11 (VP9) for decoder type 6.
        hook(CREATE_SESSION, 0x15F77 - 0x15E94, bytes.fromhex("c70013000000"), bytes.fromhex("c70011000000"))
        # 3. Processor allocation for type 6: room for the backend state after the AV1 processor's 0x48 bytes.
        if state_size + 0x48 > 0x7FFFFFFF:
            raise ValueError("state too large")
        hook(CREATE_PROCESSOR, 0xDE9A - 0xDD90, bytes.fromhex("bf48000000"), b"\xbf" + struct.pack("<I", 0x48 + state_size))
        # 4. AvdVAContext::DecodePicture: type 0x1000 -> backend entry (the AV1 branch is dropped).
        branch = names[DECODE_PICTURE] + 0x5ED34 - 0x5EAC2
        original = bytes.fromhex("41bc00000000" "4881f900000001") + cache.read(branch + 13, 6)
        if original[13:15] != b"\x0f\x85" or branch + 19 + struct.unpack_from("<i", original, 15)[0] != epilogue:
            raise ValueError("AvdVAContext::DecodePicture AV1 branch not found")
        replacement = (bytes.fromhex("4881f900100000") + b"\x0f\x85" + rel32(branch + 7, 6, epilogue)
                       + b"\xe9" + rel32(branch + 13, 5, defined["_vp9DecodePictureEntry"]) + b"\x90")
        hook(DECODE_PICTURE, branch - names[DECODE_PICTURE], original, replacement)
        # 5. Capability list: AppleGVA's VP9 decoder only creates a context when the driver advertises kind 1 type
        # 0x1000. Function 6 (AV1: 'bwar' in, NV12 out) gets type 0x1000 from a stub in the region.
        call = names[SETUP_CORE_FUNCTIONS] + 0x15CE - 0x155C
        original = b"\xe8" + rel32(call, 5, names[EXTERNALS["_nrGetAVDFuncSubId"]])
        hook(SETUP_CORE_FUNCTIONS, call - names[SETUP_CORE_FUNCTIONS], original,
             b"\xe8" + rel32(call, 5, defined["_vp9GetAVDFuncSubId"]))
        # 9. Output formats: function 6 offers NV12 only, so AppleGVA refuses 10-bit streams at context creation.
        call = names[SETUP_CORE_FUNCTIONS] + 0x163C - 0x155C
        hook(SETUP_CORE_FUNCTIONS, call - names[SETUP_CORE_FUNCTIONS],
             b"\xe8" + rel32(call, 5, names[EXTERNALS["_nrGetAVDFuncOutputFormatInfo"]]),
             b"\xe8" + rel32(call, 5, defined["_vp9GetAVDFuncOutputFormatInfo"]))
        # 6. Output buffer layout for kind 1: type 0x1000 takes the NV12 path AV1 had (type 0x1000 was refused).
        hook(GET_BUFFER_REQUIREMENT, 0x5E6EF - 0x5E514, bytes.fromhex("81fa00000001"), bytes.fromhex("81fa00100000"))
        # 7. Context parameter 0xb (size limits): type 0x1000 reads the limits the kernel reports for AV1.
        hook(GET_CONTEXT_PARAMETER, 0x5D108 - 0x5D0A0, bytes.fromhex("4981f900000001"),
             bytes.fromhex("4981f900100000"))
        # 8. initDriverCapability keeps a function only if its codec bit is set in the hardware mask; function 6
        # tests bit 7 (AV1, absent on VCN 2) and is dropped from the list AppleGVA sees. Test bit 6 (HEVC) instead.
        hook(INIT_CAPABILITY, 0x13F3 - 0x10C4, bytes.fromhex("bf07000000"), bytes.fromhex("bf06000000"))

        # ---- Resolver data: hooked functions plus the region as one function ----
        resolver = ResolverData(cache, WORK)
        records, functions = [], []
        for function, address, code, new, sites in hooks:
            index = resolver.add(address, code, bytes(new))
            for offset, size in sites:
                records.append((index, offset, code[offset:offset + size], bytes(new[offset:offset + size]), function))
        # Region references: the original ones per AV1 method (no data is decoded as code), the patched ones from
        # the unchanged bytes plus the linker's external fields. Internal targets need no rebuilding.
        image, image_base = resolver.owner(start)
        original_refs = []
        for i, (address, name) in enumerate(members):
            size = (members[i + 1][0] if i + 1 < len(members) else end) - address
            for offset, img, rva, indirect in resolver.references(region[address - start:address - start + size],
                                                                   address, f"av1-{i}"):
                original_refs.append((address - start + offset, img, rva, indirect))
        patched_refs = [r for r in original_refs if patched[r[0]:r[0] + 4] == region[r[0]:r[0] + 4]
                        and all(patched[r[0] - k] == region[r[0] - k] for k in (1, 2))]
        for field, target in external_fields:
            img, img_base = resolver.owner(target)
            patched_refs.append((field - start, img, target - img_base, 0))
        patched_refs.sort()
        region_index = len(resolver.functions)
        resolver.functions.append((image, start - image_base, region, original_refs, patched_refs))

        # Sites: changed bytes in pieces of at most MAX_SITE bytes that stay inside one page.
        changed = [i for i in range(len(region)) if region[i] != patched[i]]
        fields = sorted({r[0] for r in original_refs} | {r[0] for r in patched_refs})
        sites, i = [], 0
        while i < len(changed):
            first = changed[i]
            page_end = ((start + first) // 4096 + 1) * 4096 - start
            limit = min(first + MAX_SITE, page_end)
            last_changed = first
            while i < len(changed) and changed[i] < limit and changed[i] - last_changed <= 192:
                last_changed = changed[i]
                i += 1
            # Never cut inside a rel32 field (original or patched): end the site before the field instead.
            stop = last_changed + 1
            split = [r for r in fields if r < stop < r + 4]
            if split:
                stop = min(split)
                while i > 0 and changed[i - 1] >= stop:
                    i -= 1
                if stop <= first:
                    raise ValueError("cannot place a site boundary outside a rel32 field")
            sites.append((first, stop))
        for first, stop in sites:
            records.append((region_index, first, region[first:stop], bytes(patched[first:stop]), "AV1 region"))
        records.sort(key=lambda r: resolver.functions[r[0]][1] + r[1])
        if len(records) > 32:
            raise ValueError(f"too many patch sites: {len(records)}")
        # A patched rel32 field must lie fully inside one site.
        for idx, (_, _, _, refs, prefs) in enumerate(resolver.functions):
            for offset, *_ in list(refs) + list(prefs):
                covering = [r for r in records if r[0] == idx and r[1] <= offset + 3 and offset < r[1] + len(r[2])]
                if covering and not (covering[0][1] <= offset and offset + 4 <= covering[0][1] + len(covering[0][2])):
                    raise ValueError(f"rel32 field split between sites: function {idx} offset {offset:#x}, "
                                     f"site {covering[0][1]:#x}+{len(covering[0][2]):#x} ({covering[0][4]})")

        # ---- Header ----
        header = ("// Generated by tools/build_vp9_decode_patch.py from tools/vp9_backend/VP9Backend.cpp; do not edit.\n"
                  "// VP9 plan, phase V3 (-NRedVP9). Backend code: Mesa port, MIT (see NootedRed/VP9Message.hpp).\n"
                  "#pragma once\n#include <IOKit/IOTypes.h>\n#include <UserCacheResolver.hpp>\n#include <UserCacheSectionData.hpp>\n#include <UserVideoDecodeData.hpp>\n"
                  "namespace UserVP9DecodeData {\nusing Patch = UserVideoDecodeData::Patch;\n")
        header += array("CacheUUID", cache.uuid)
        for i, (_, _, original, replacement, label) in enumerate(records):
            header += f"// {label}\n" + array(f"Original{i}", original) + array(f"Patched{i}", replacement)
        header += "inline constexpr Patch Patches[] = {\n"
        locations = []
        for i, (function, offset, original, _, _) in enumerate(records):
            address = resolver.functions[function][1] + image_base + offset
            if address // 4096 != (address + len(original) - 1) // 4096:
                raise ValueError("site crosses a page")
            header += f"    {{0x{address - cache.base:X}ULL, Original{i}, Patched{i}, sizeof(Original{i})}},\n"
            locations.append((function, offset))
        header += "};\n"
        header += resolver.header(locations)
        header += resolver.section_header()
        pages = len({(resolver.functions[f][1] + image_base + o) // 4096 for f, o, *_ in records})
        header += (f"inline constexpr UInt32 MaxPatchSize = {MAX_SITE};\ninline constexpr UInt32 PageCount = {pages};\n"
                   f"inline constexpr UInt32 StateSize = 0x{state_size:X};\n}}    // namespace UserVP9DecodeData\n")
        output = ROOT / "NootedRed/UserVP9DecodeData.hpp"
        if args.check:
            if not output.exists() or output.read_text() != header:
                raise ValueError(f"generated data out of date: {output}")
        else:
            output.write_text(header)

        # Offline view of the result for review and tests.
        image_copy = {"region": [start, patched.hex()], "hooks": [[a, bytes(n).hex()] for _, a, _, n, _ in hooks]}
        (WORK / "vp9-patch.json").write_text(json.dumps({
            "cacheUUID": cache.uuid.hex(), "imageBase": image_base, "sites": len(records), "pages": pages,
            "stateSize": state_size, "codeBytes": sum(s["size"] for s in sections if s["name"] == "__TEXT,__text"),
            "dataBytes": sum(s["size"] for s in sections if s["name"] != "__TEXT,__text"),
            "codeRoom": init_context - code_start, "dataRoom": last - data_start,
            "symbols": {k: v for k, v in defined.items() if k.startswith("_vp9")},
            "externals": externals, "patched": image_copy,
            "sha256": hashlib.sha256(header.encode()).hexdigest()}, indent=1) + "\n")
        print(f"VP9 backend: {len(records)} sites on {pages} pages, code "
              f"{sum(s['size'] for s in sections if s['name'] == '__TEXT,__text')}/{init_context - code_start} B, "
              f"state {state_size} B, refs {len(original_refs)} original / {len(patched_refs)} patched")
    finally:
        cache.close()


if __name__ == "__main__":
    sys.exit(main())
