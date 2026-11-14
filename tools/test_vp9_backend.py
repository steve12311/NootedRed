"""VP9 plan, phase V3: offline test of the VP9 backend blob that tools/build_vp9_decode_patch.py puts in the AMD driver.

The same object file the generator links is linked here at a test address with trampolines to stub driver
functions, mapped executable by tools/fixtures/vp9-backend-harness.cpp and run on the libvpx clips of
tools/test_vp9_message.py. Every VCN message is compared with VP9Message compiled natively (itself equal to Mesa),
and the generic parts (header, decode message, Tier 1 DPB, command buffers) with what Mesa's ac_vcn_dec.c and the AMD
driver's HEVC path write. Also checks the refusals and the AvdVAContext::DecodePicture branch.

Usage: python3 tools/test_vp9_backend.py   (needs ffmpeg with libvpx for the clips, like test_vp9_message.py)
"""
import struct
import subprocess
import sys
from pathlib import Path

import build_vp9_decode_patch as gen
import test_vp9_message as v2

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build/vp9/backend-test"
BLOB_BASE, BLOB_SIZE, CODE, DATA, TRAMPOLINES, TABLE = 0x200000000, 0x4000, 0x100, 0x2000, 0x3000, 0x3800
STUBS = ["_nrProcessVcnCreateDecodeSession", "_nrPrepareBitStream", "_nrGetSegFgBuffer", "_nrGetIOSurfaceBuffer",
         "_nrSetRefCon", "_nrDecodePictureEpilogue", "_nrGetAVDFuncSubId"]


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    obj = WORK / "VP9Backend.o"
    subprocess.run(["xcrun", "clang++", *gen.CFLAGS, "-c", str(ROOT / "tools/vp9_backend/VP9Backend.cpp"), "-o",
                    str(obj)], check=True, timeout=120)
    sections, syms = gen.read_object(obj)
    placement, cursor = {}, BLOB_BASE + DATA
    for s in sections:
        if s["name"] == "__TEXT,__text":
            placement[s["name"]] = BLOB_BASE + CODE
            if CODE + s["size"] > DATA:
                raise ValueError("test layout: code too large")
        elif s["size"]:
            cursor = (cursor + s["align"] - 1) & ~(s["align"] - 1)
            placement[s["name"]] = cursor
            cursor += s["size"]
    if cursor > BLOB_BASE + TRAMPOLINES:
        raise ValueError("test layout: data too large")
    blob = bytearray(BLOB_SIZE)
    externals = {}
    for i, name in enumerate(STUBS):    # jmp *table[i](%rip)
        source = BLOB_BASE + TRAMPOLINES + 16 * i
        blob[source - BLOB_BASE:source - BLOB_BASE + 6] = b"\xff\x25" + gen.rel32(source, 6, BLOB_BASE + TABLE + 8 * i)
        externals[name] = source
    chunks, defined, fields = gen.link(sections, syms, placement, externals)
    for address, code in chunks.items():
        blob[address - BLOB_BASE:address - BLOB_BASE + len(code)] = code
    if sorted({t for _, t in fields}) != sorted(externals.values()):
        raise ValueError("backend does not call every driver function it declares")
    (WORK / "blob.bin").write_bytes(blob)
    const = placement["__TEXT,__const"]
    state_size = struct.unpack_from("<I", blob, defined["_vp9StateSize"] - BLOB_BASE)[0]
    off = lambda n: defined[n] - BLOB_BASE
    (WORK / "vp9-backend-symbols.h").write_text(
        f"constexpr uintptr_t BlobBase = {BLOB_BASE:#x};\nconstexpr size_t BlobSize = {BLOB_SIZE:#x}, "
        f"TableOffset = {TABLE:#x}, StateSize = {state_size};\n"
        f"constexpr size_t SymCreate = {off('_vp9ProcessCreateDecodeSession'):#x}, "
        f"SymDpb = {off('_vp9CalculateDPBSize'):#x}, SymContext = {off('_vp9CalculateContextSize'):#x}, "
        f"SymInit = {off('_vp9InitHWContext'):#x}, SymArea = {off('_vp9GetDecodeTargetArea'):#x}, "
        f"SymBitstream = {off('_vp9SetBitstreamFields'):#x}, SymMessage = {off('_vp9SetDecodeMessageFields'):#x}, "
        f"SymDecode = {off('_vp9DecodePicture'):#x};\n")
    del const
    exe = WORK / "harness"
    subprocess.run(["xcrun", "clang++", "-std=c++17", "-O1", "-g", f"-I{ROOT / 'NootedRed'}", f"-I{WORK}",
                    str(ROOT / "tools/fixtures/vp9-backend-harness.cpp"), "-o", str(exe)], check=True, timeout=120)
    v2.WORK.mkdir(parents=True, exist_ok=True)
    args = [str(exe), str(WORK / "blob.bin")]
    for name, size, frames, options, pix_fmt, _ in v2.CLIPS:
        ivf = v2.make_clip(name, size, frames, options, pix_fmt)
        width, height = size.split("x")
        args += [str(ivf), width, height, "0" if pix_fmt.endswith("10le") else "1"]
    result = subprocess.run(args, timeout=300)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
