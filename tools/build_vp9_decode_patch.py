"""VP9 plan, phase V3: build the VP9 backend patch for AMDRadeonVADriver2.

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
from metal_cache import CACHE, CacheReader, array
import vt_decoder_selection

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vp9/backend"
IMAGE = "/System/Library/Extensions/AMDRadeonVADriver2.bundle/Contents/MacOS/AMDRadeonVADriver2"
DEFAULT_UUID = "5a3da575f02e33dc9b14f663050011e6"
MAX_SITE = 512
PROFILES = {
    23: ("Sonoma", "4c1441fd8a543dc2863d49110b946a25"),
    24: ("Sequoia", "0faee1e17a093a16922ed6e1663c715f"),
    25: ("", DEFAULT_UUID),
}
DRIVER_OFFSETS = {
    23: (0x16F, 0xF2, 0x106, 0x243, 0x5AD, 0x58, 0xD8, 0x1F2, 0x6C, 0x2F5),
    24: (0x179, 0xE1, 0x10A, 0x20E, 0x41, 0x72, 0xE0, 0x1DB, 0x6C, 0x331),
    25: (0x179, 0xE3, 0x10A, 0x272, 0x41, 0x72, 0xE0, 0x1DB, 0x68, 0x32F),
}

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
    parser.add_argument("--major", type=int, choices=PROFILES, default=25)
    parser.add_argument("--uuid", help="UUID of the reference shared cache")
    parser.add_argument("--cache", type=Path, default=CACHE, help="只读参考共享缓存目录")
    parser.add_argument("--check", action="store_true", help="verify the checked-in header instead of writing it")
    args = parser.parse_args()
    label, reference_uuid = PROFILES[args.major]
    (function_type, create_session, create_processor, decode_branch, epilogue_offset,
     capability_id, capability_format, buffer_requirement, context_parameter,
     init_capability) = DRIVER_OFFSETS[args.major]
    namespace = f"UserVP9Decode{label}Data"
    WORK.mkdir(parents=True, exist_ok=True)

    obj = WORK / "VP9Backend.o"
    subprocess.run(["xcrun", "clang++", *CFLAGS, "-c", str(ROOT / "tools/vp9_backend/VP9Backend.cpp"), "-o", str(obj)],
                   check=True, timeout=120)
    sections, syms = read_object(obj)

    cache = CacheReader(args.cache, uuid=args.uuid or reference_uuid)
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
        helper_start = (names[SLOTS[0x78][0]] + 5 + 15) & ~15
        placement, cursor = {}, code_start
        for s in sections:
            if s["name"] == "__TEXT,__text":
                placement[s["name"]] = cursor
                cursor += s["size"]
            elif s["name"] == "__TEXT,__nrhelpers":
                if helper_start + s["size"] > message_fields:
                    raise ValueError("backend helpers exceed the replaced bitstream method")
                placement[s["name"]] = helper_start
        if cursor > init_context:
            raise ValueError(f"backend code too large: {cursor - code_start} bytes, room {init_context - code_start}")
        cursor = data_start
        for s in sections:
            if s["name"] not in ("__TEXT,__text", "__TEXT,__nrhelpers") and s["size"]:
                cursor = (cursor + s["align"] - 1) & ~(s["align"] - 1)
                placement[s["name"]] = cursor
                cursor += s["size"]
        if cursor > last:
            raise ValueError(f"backend data too large: {cursor - data_start} bytes, room {last - data_start}")
        resolver = ResolverData(cache, WORK)
        selection_address, selection_code = vt_decoder_selection.prepare(cache, args.major)
        externals = {k: names[v] for k, v in EXTERNALS.items()}
        epilogue = names[DECODE_PICTURE] + epilogue_offset
        if cache.read(epilogue, 3) != bytes.fromhex("4489e0"):    # mov eax, r12d
            raise ValueError("AvdVAContext::DecodePicture epilogue not found")
        externals["_nrDecodePictureEpilogue"] = epilogue
        requirement, requirement_size = extent(GET_BUFFER_REQUIREMENT)
        requirement_code = cache.read(requirement, requirement_size)
        av1_requirement = bytes.fromhex("81fa000000010f84")
        p010_requirement = bytes.fromhex("81f93032347875940fbae210738e837e2002")
        if (requirement_code[buffer_requirement:buffer_requirement + len(av1_requirement)] != av1_requirement
                or requirement_code.count(p010_requirement) != 1):
            raise ValueError("未找到预期的 AV1 和 P010 布局分支")
        nv12 = buffer_requirement + 12 + struct.unpack_from("<i", requirement_code, buffer_requirement + 8)[0]
        if requirement_code[nv12:nv12 + 4] != bytes.fromhex("837e0c00"):
            raise ValueError("未找到预期的 NV12 布局分支")
        externals["_nrBufferRequirementNV12"] = requirement + nv12
        externals["_nrBufferRequirementP010"] = requirement + requirement_code.index(p010_requirement) + 14
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
        hook(GET_FUNCTION_TYPE, function_type, bytes.fromhex("483d00000001"), bytes.fromhex("483d00100000"))
        # 2. Create message: codec 0x13 (AV1) -> 0x11 (VP9) for decoder type 6.
        hook(CREATE_SESSION, create_session, bytes.fromhex("c70013000000"), bytes.fromhex("c70011000000"))
        # 3. Processor allocation for type 6: room for the backend state after the AV1 processor's 0x48 bytes.
        if state_size + 0x48 > 0x7FFFFFFF:
            raise ValueError("state too large")
        hook(CREATE_PROCESSOR, create_processor, bytes.fromhex("bf48000000"), b"\xbf" + struct.pack("<I", 0x48 + state_size))
        # 4. AvdVAContext::DecodePicture: type 0x1000 -> backend entry (the AV1 branch is dropped).
        branch = names[DECODE_PICTURE] + decode_branch
        prefix = bytes.fromhex("41bc00000000") if args.major == 25 else b""
        compare = prefix + bytes.fromhex("4881f9000000010f85")
        native_jump = cache.read(branch + len(compare), 4)
        if branch + len(compare) + 4 + struct.unpack("<i", native_jump)[0] != epilogue:
            raise ValueError("AvdVAContext::DecodePicture AV1 branch not found")
        tail = b"" if args.major == 25 else bytes.fromhex("4c8b6b18488b4320")
        original = compare + native_jump + tail
        replacement = (bytes.fromhex("4881f900100000") + b"\x0f\x85" + rel32(branch + 7, 6, epilogue)
                       + b"\xe9" + rel32(branch + 13, 5, defined["_vp9DecodePictureEntry"]))
        replacement += b"\x90" * (len(original) - len(replacement))
        hook(DECODE_PICTURE, branch - names[DECODE_PICTURE], original, replacement)
        # 5. Capability list: AppleGVA's VP9 decoder only creates a context when the driver advertises kind 1 type
        # 0x1000. Function 6 (AV1: 'bwar' in, NV12 out) gets type 0x1000 from a stub in the region.
        call = names[SETUP_CORE_FUNCTIONS] + capability_id
        original = b"\xe8" + rel32(call, 5, names[EXTERNALS["_nrGetAVDFuncSubId"]])
        hook(SETUP_CORE_FUNCTIONS, call - names[SETUP_CORE_FUNCTIONS], original,
             b"\xe8" + rel32(call, 5, defined["_vp9GetAVDFuncSubId"]))
        # 函数 6 公布 NV12 和 x420。
        call = names[SETUP_CORE_FUNCTIONS] + capability_format
        hook(SETUP_CORE_FUNCTIONS, call - names[SETUP_CORE_FUNCTIONS],
             b"\xe8" + rel32(call, 5, names[EXTERNALS["_nrGetAVDFuncOutputFormatInfo"]]),
             b"\xe8" + rel32(call, 5, defined["_vp9GetAVDFuncOutputFormatInfo"]))
        # VP9 复用驱动的 NV12 和 x420 平面布局。
        branch = requirement + buffer_requirement
        hook(GET_BUFFER_REQUIREMENT, buffer_requirement, requirement_code[buffer_requirement:buffer_requirement + 12],
             bytes.fromhex("81fa001000000f84") + rel32(branch + 6, 6, defined["_vp9BufferRequirement"]))
        # 7. Context parameter 0xb (size limits): type 0x1000 reads the limits the kernel reports for AV1.
        hook(GET_CONTEXT_PARAMETER, context_parameter, bytes.fromhex("4981f900000001"),
             bytes.fromhex("4981f900100000"))
        # 8. initDriverCapability keeps a function only if its codec bit is set in the hardware mask; function 6
        # tests bit 7 (AV1, absent on VCN 2) and is dropped from the list AppleGVA sees. Test bit 6 (HEVC) instead.
        hook(INIT_CAPABILITY, init_capability, bytes.fromhex("bf07000000"), bytes.fromhex("bf06000000"))

        # ---- Resolver data: hooked functions plus the region as one function ----
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

        # 后端直接访问的对象布局和虚函数调用也必须来自完整、已匹配的实现。
        abi_names = set(EXTERNALS.values()) | {
            "__ZN25AvdVcnAv1DecDataProcessorC2EP9VADecoder",
            "__ZN19DecodeDataProcessorC2EP9VADecoder",
            "__ZN9VADecoderC2Ev", "__ZN12VAVcnDecoderC2Ev",
            "__ZN9VADecoder13DecodePictureEPvP15VAVendorTextureS2_",
        }
        for name in sorted(abi_names, key=lambda n: names[n]):
            address, size = extent(name)
            if any(image_base + rva <= address < image_base + rva + len(code)
                   for _, rva, code, _, _ in resolver.functions):
                continue
            if 'C2' in name and names.get(name.replace('C2', 'C1')) == address + size:
                size += extent(name.replace('C2', 'C1'))[1]
            while size < 32:
                following = address + size
                size += ordered[ordered.index(following) + 1] - following
            native = cache.read(address, size)
            resolver.add(address, native, native)

        abi_slots = []
        for constructor, table_name, slots in (
            ("__ZN25AvdVcnAv1DecDataProcessorC2EP9VADecoder", "__ZTV25AvdVcnAv1DecDataProcessor",
             range(0x20, 0x88, 8)),
            ("__ZN9VADecoderC2Ev", "__ZTV9VADecoder", (0x58,)),
            ("__ZN12VAVcnDecoderC2Ev", "__ZTV12VAVcnDecoder", (0x58,)),
        ):
            table = names[table_name]
            ctor_index = next(i for i, (_, rva, code, _, _) in enumerate(resolver.functions)
                              if image_base + rva <= names[constructor] < image_base + rva + len(code))
            ctor = resolver.functions[ctor_index]
            field = next(off for off, img, rva, indirect in ctor[3]
                         if not indirect and resolver.images[img][1] + rva == table + 0x10)
            for slot in slots:
                destination = resolver.pointer(table + slot)
                target_index = next((i for i, (_, rva, code, _, _) in enumerate(resolver.functions)
                                     if image_base + rva <= destination < image_base + rva + len(code)), None)
                if target_index is None:
                    size = ordered[ordered.index(destination) + 1] - destination
                    while size < 32:
                        size += ordered[ordered.index(destination + size) + 1] - destination - size
                    native = cache.read(destination, size)
                    target_index = resolver.add(destination, native, native)
                offset = destination - image_base - resolver.functions[target_index][1]
                abi_slots.append((ctor_index, field, slot - 0x10, target_index, offset))

        selection_patched = vt_decoder_selection.patched(selection_code, args.major)
        start_fourcc = vt_decoder_selection.LAYOUTS[args.major][3]
        end_fourcc = start_fourcc + len(vt_decoder_selection.FOURCC)
        selection_index = resolver.add(selection_address, selection_code, selection_patched)
        records.append((selection_index, start_fourcc, selection_code[start_fourcc:end_fourcc],
                        selection_patched[start_fourcc:end_fourcc], 'VideoToolbox decoder admission'))

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
        def function_address(index):
            function = resolver.functions[index]
            return resolver.images[function[0]][1] + function[1]

        records.sort(key=lambda r: function_address(r[0]) + r[1])
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
                  "// VP9 backend. Code: Mesa port, MIT (see NootedRed/VP9Message.hpp).\n"
                  "#pragma once\n#include <IOKit/IOTypes.h>\n#include <UserCacheResolver.hpp>\n#include <UserCacheSectionData.hpp>\n#include <UserVideoDecodeData.hpp>\n"
                  f"namespace {namespace} {{\nusing Patch = UserVideoDecodeData::Patch;\n")
        header += array("CacheUUID", cache.uuid)
        for i, (_, _, original, replacement, label) in enumerate(records):
            header += f"// {label}\n" + array(f"Original{i}", original) + array(f"Patched{i}", replacement)
        header += "inline constexpr Patch Patches[] = {\n"
        locations = []
        for i, (function, offset, original, _, _) in enumerate(records):
            address = function_address(function) + offset
            if address // 4096 != (address + len(original) - 1) // 4096:
                raise ValueError("site crosses a page")
            header += f"    {{0x{address - cache.base:X}ULL, Original{i}, Patched{i}, sizeof(Original{i})}},\n"
            locations.append((function, offset))
        header += "};\n"
        header += resolver.header(locations)
        header += resolver.section_header()
        header += ("struct ABISlot { UInt32 constructor, field, slot, function, offset; };\n"
                   "inline constexpr ABISlot ABISlots[] = {\n")
        header += ''.join('    {' + ', '.join(map(str, row)) + '},\n' for row in abi_slots) + '};\n'
        admission_patch = next(i for i, row in enumerate(records) if row[0] == selection_index)
        header += f'inline constexpr UInt32 DecoderAdmissionPatch = {admission_patch};\n'
        pages = len({(function_address(f) + o) // 4096 for f, o, *_ in records})
        header += (f"inline constexpr UInt32 MaxPatchSize = {MAX_SITE};\ninline constexpr UInt32 PageCount = {pages};\n"
                   f"inline constexpr UInt32 StateSize = 0x{state_size:X};\n}}    // namespace {namespace}\n")
        output = ROOT / "NootedRed" / f"{namespace}.hpp"
        if args.check:
            if not output.exists() or output.read_text() != header:
                raise ValueError(f"generated data out of date: {output}")
        else:
            output.write_text(header)

        # Offline view of the result for review and tests.
        image_copy = {"region": [start, patched.hex()], "hooks": [[a, bytes(n).hex()] for _, a, _, n, _ in hooks]}
        (WORK / (f"vp9-patch-{args.major}.json" if label else "vp9-patch.json")).write_text(json.dumps({
            "cacheUUID": cache.uuid.hex(), "imageBase": image_base, "sites": len(records), "pages": pages,
            "stateSize": state_size, "codeBytes": sum(s["size"] for s in sections if s["name"] == "__TEXT,__text"),
            "dataBytes": sum(s["size"] for s in sections if s["name"] != "__TEXT,__text"),
            "codeRoom": init_context - code_start, "dataRoom": last - data_start,
            "symbols": {k: v for k, v in defined.items() if k.startswith("_vp9")},
            "externals": externals, "patched": image_copy,
            "selection": {"address": selection_address, "original": selection_code.hex(),
                          "patched": selection_patched.hex()},
            "sha256": hashlib.sha256(header.encode()).hexdigest()}, indent=1) + "\n")
        print(f"VP9 backend: {len(records)} sites on {pages} pages, code "
              f"{sum(s['size'] for s in sections if s['name'] == '__TEXT,__text')}/{init_context - code_start} B, "
              f"state {state_size} B, refs {len(original_refs)} original / {len(patched_refs)} patched")
    finally:
        cache.close()


if __name__ == "__main__":
    sys.exit(main())
