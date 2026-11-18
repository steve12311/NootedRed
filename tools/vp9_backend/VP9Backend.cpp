//! VP9 plan, phase V3: the VP9 backend that replaces the AV1 decode processor of AMDRadeonVADriver2.
//!
//! This file is not part of the kext. tools/build_vp9_decode_patch.py compiles it freestanding for the user process
//! that loads the AMD video driver (VTDecoderXPCService and friends), links it at the address of the AV1 code
//! (never run on VCN 2, which has no AV1) and turns it into patch sites. Everything here runs inside the AMD
//! driver, with its structures, so the offsets below are the driver's (docs/vp9-v0-findings.md, "V3").
//!
//! The VCN message itself comes from NootedRed/VP9Message.hpp (Mesa port, checked byte for byte in V2). This file
//! adds what Mesa's ac_vcn_dec.c does around it for VCN 2 (generic decode message, Tier 1 dynamic DPB) and the glue
//! to the driver's buffers. Copyright notice of the Mesa parts: see VP9Message.hpp (MIT).

#include <VP9DefaultProbs.hpp>
#include <VP9Message.hpp>

using namespace VP9Message;

// ---- Driver entry points (resolved by the generator from the driver's own symbols) ---------------------------------

extern "C" {
//! DecodeDataProcessor::ProcessVcnCreateDecodeSession(_uvd_command_build_info*)
int nrProcessVcnCreateDecodeSession(void *processor, void *build);
//! DecodeDataProcessor::PrepareBitStream(_decrypt_info*)
int nrPrepareBitStream(void *processor, void *decrypt);
//! VADecoder::getSegFgBuffer(): advances the probability ring, waits for the GPU and returns its CPU address.
uint8_t *nrGetSegFgBuffer(void *decoder);
//! VAContext::getIOSurfaceBuffer(__IOSurface*, VAVendorTexture**, unsigned int, unsigned int*, bool)
int nrGetIOSurfaceBuffer(void *context, void *surface, void **texture, uint32_t plane, uint32_t *offset, bool wait);
//! VAContext::setRefCon(_CommandRefCon*, VAVendorTexture*, void*)
void nrSetRefCon(void *context, void *refCon, void *texture, void *command);
}

// No libc in the driver's text: the compiler may still emit these for struct copies and zeroing.
extern "C" __attribute__((section("__TEXT,__nrhelpers"))) void* memset(void* dst, int value, size_t size)
{
    void *ret = dst;
    asm volatile("rep stosb" : "+D"(dst), "+c"(size) : "a"(value) : "memory");
    return ret;
}
extern "C" __attribute__((section("__TEXT,__nrhelpers"))) void* memcpy(void* dst, const void* src, size_t size)
{
    void *ret = dst;
    asm volatile("rep movsb" : "+D"(dst), "+S"(src), "+c"(size) : : "memory");
    return ret;
}
inline void *operator new(size_t, void *where) noexcept { return where; }

namespace {

template<typename T>
T &field(void *base, size_t offset) {
    return *reinterpret_cast<T *>(static_cast<uint8_t *>(base) + offset);
}

// ---- AMD driver layout ---------------------------------------------------------------------------------------------

namespace Proc {    // AvdVcnAv1DecDataProcessor / DecodeDataProcessor
constexpr size_t Decoder = 0x10, Config = 0x18, FrameCounter = 0x20, State = 0x48;
}
namespace Dec {    // VADecoder / VAVcnDecoder
constexpr size_t Session = 0x58, Width = 0x7C, Height = 0x80, MaxBitstream = 0x8C, Decrypt = 0x98;
}
namespace Sess {    // decode session block (VADecoder+0x58)
constexpr size_t Dirty = 0x0C, DpbSize = 0x30, Dpb = 0x38, MessageTextures = 0x98, MessageIndex = 0xD8,
                 Message = 0xE0, ContextSize = 0x1C8, Context = 0x1D0, ItTexture = 0x1E0, ProbTextures = 0x1F0,
                 ProbIndex = 0x270, Bitstream = 0x2A8, BitstreamSize = 0x2B0;
}
namespace Tex {    // VAVendorTexture
constexpr size_t Height = 0x7C, Pitch = 0x80, BytesPerElement = 0x84, Cpu = 0x98;
}
namespace Build {    // _uvd_command_build_info
constexpr size_t MessageOn = 0x04, Message = 0x08, DpbOn = 0x10, Dpb = 0x18, BitstreamOn = 0x20, Bitstream = 0x28,
                 BitstreamOffset = 0x30, TargetOn = 0x70, Target = 0x78, TargetOffset = 0x80, ProbOn = 0x90,
                 Prob = 0x98, ContextOn = 0xA0, Context = 0xA8, ItOn = 0xB0, It = 0xB8;
}
namespace Cfg {    // [[processor+0x18]+0x460]
constexpr size_t Root = 0x460, Flags94 = 0x94, Flags95 = 0x95;
constexpr uint8_t DirectBitstream = 0x80, ItBuffer = 0x80;
}
namespace Pic {    // kAVDPictureDecodeVP9 parameters (P)
constexpr size_t Frame = 0x0, FrameSize = 0x8, Target = 0x1250, Refs = 0x1260, RefStride = 0x1258;
}
namespace Ctx {    // AvdVAContext
constexpr size_t Va = 0x08, Pool0 = 0x30, Pool1 = 0x140, PoolStride = 0x110;
constexpr size_t PoolValid = 0x20, PoolSize = 0x28, PoolCount = 0x2C;    // relative to the context
}
namespace Cmd {    // AVD_CmdInfo
constexpr size_t Decoder = 0x18, Params = 0x20;
}

// ---- VCN message (ac_vcn_dec.h) ------------------------------------------------------------------------------------

constexpr uint32_t MessageDecode = 2, MessageVp9 = 0x0E, MessageDynamicDpb = 0x10, MsgTypeDecode = 1;
constexpr uint32_t FlagUseDynamicDpb = 0x1, FlagUsePal = 0x8;
constexpr uint32_t DpbSlots = 9, DpbAlignment = 64;

struct MessageIndex {
    uint32_t id, offset, size, filled;
};
struct MessageHeader {    // rvcn_dec_message_header_t with its three index entries
    uint32_t headerSize, totalSize, numBuffers, msgType, streamHandle, feedbackNumber;
    MessageIndex index[3];
};
struct DecodeMessage {    // rvcn_dec_message_decode_t
    uint32_t streamType, decodeFlags, width, height, bsdSize, dpbSize, dtSize, sctSize, scCoeffSize, hwCtxtSize,
        swCtxtSize, picParamSize, mbCntlSize, reserved0[4], decodeBufferFlags;
    uint32_t dbPitch, dbAlignedHeight, dbTilingMode, dbSwizzleMode, dbArrayMode, dbFieldMode, dbSurfTileConfig;
    uint32_t dtPitch, dtUvPitch, dtTilingMode, dtSwizzleMode, dtArrayMode, dtFieldMode, dtOutFormat, dtSurfTileConfig,
        dtUvSurfTileConfig, dtLumaTopOffset, dtLumaBottomOffset, dtChromaTopOffset, dtChromaBottomOffset,
        dtChromaVTopOffset, dtChromaVBottomOffset;
    uint32_t mifWrcEn, dbPitchUv;
    uint8_t reserved1[20];
};
struct DynamicDpb {    // rvcn_dec_message_dynamic_dpb_t
    uint32_t configFlags, lumaPitch, lumaAlignedHeight, lumaAlignedSize, chromaPitch, chromaAlignedHeight,
        chromaAlignedSize;
    uint8_t arraySize, curArraySlice, refArraySlice[16], reserved0[2];
    uint32_t currOffset, addrOffset[16];
};
static_assert(sizeof(MessageHeader) == 0x48 && sizeof(DecodeMessage) == 0xBC && sizeof(DynamicDpb) == 0x74);
static_assert(sizeof(Message) == 0x78 && sizeof(ProbsSegment) == 2560);

constexpr uint32_t OffsetDecode = sizeof(MessageHeader), OffsetDpb = OffsetDecode + sizeof(DecodeMessage),
                   OffsetVp9 = OffsetDpb + sizeof(DynamicDpb), MessageSize = OffsetVp9 + sizeof(Message);
static_assert(MessageSize == 0x1F0 && MessageSize <= 0x1008);

// ---- Backend state, after the 0x48 bytes of the AV1 processor ------------------------------------------------------

constexpr uint64_t StateMagic = 0x4E52564539535431ULL;    // "NRVE9ST1"

struct State {
    uint64_t magic;
    uint32_t maxWidth, maxHeight;      // session size: the DPB is sized once from these
    uint32_t width, height;            // last parsed frame, for getDecodeTargetArea
    uint32_t bytesPerSample;
    Session session;
};

State *state(void *processor) {
    auto *s = &field<State>(processor, Proc::State);
    return s->magic == StateMagic ? s : nullptr;
}

constexpr uint32_t align(uint32_t v, uint32_t a) { return (v + a - 1) & ~(a - 1); }

struct DpbGeometry {
    uint32_t pitch, height, lumaSize, chromaSize;
};

DpbGeometry dpbGeometry(const State &s) {
    const uint32_t pitch = align(s.maxWidth, DpbAlignment), height = align(s.maxHeight, DpbAlignment);
    const uint32_t bytes = pitch * height * s.bytesPerSample;
    return {pitch, height, bytes, bytes / 2};
}

uint8_t configFlags(void *processor, size_t offset) {
    return field<uint8_t>(field<void *>(field<void *>(processor, Proc::Config), Cfg::Root), offset);
}

}    // namespace

//! Read by the generator: bytes to add to the processor allocation (VAFactory::createDecodeDataProcessor, type 6).
extern "C" __attribute__((used)) const uint32_t vp9StateSize = sizeof(State);

// ---- Processor methods (reached through jumps at the AV1 vtable entries) -------------------------------------------

//! ProcessCreateDecodeSession: start a clean VP9 state, then the driver's create message (codec patched to VP9).
extern "C" int vp9ProcessCreateDecodeSession(void *processor, void *build) {
    void *decoder = field<void *>(processor, Proc::Decoder);
    auto *s = &field<State>(processor, Proc::State);
    memset(s, 0, sizeof(*s));
    new (&s->session) Session();
    s->maxWidth = field<uint32_t>(decoder, Dec::Width);
    s->maxHeight = field<uint32_t>(decoder, Dec::Height);
    // Up to 4096x4096 for now (VCN 2 decodes up to 8192x4352; the DPB below grows with the size). A larger stream is
    // refused here: without a session every frame would fail, and the frames would still reach the client.
    if (s->maxWidth - 1 >= 4096 || s->maxHeight - 1 >= 4096) { return 1; }
    s->magic = StateMagic;
    return nrProcessVcnCreateDecodeSession(processor, build);
}

//! CalculateDPBSize: nine Tier 1 slots (luma then chroma), as Mesa sizes its 2D-array DPB on VCN 2.
extern "C" uint32_t vp9CalculateDPBSize(void *processor) {
    const State *s = state(processor);
    if (!s) { return 0; }
    if (!s->bytesPerSample) { return 0; }
    const auto g = dpbGeometry(*s);
    return DpbSlots * (g.lumaSize + g.chromaSize);
}

extern "C" uint32_t vp9CalculateContextSize(void *processor) {
    const State *s = state(processor);
    return contextSize(s && s->bytesPerSample == 2 ? 10 : 8);
}

//! initHWContext: called by VADecoder when it (re)allocates the context buffer.
extern "C" __attribute__((section("__TEXT,__nrhelpers"))) void vp9InitHWContext(void* processor)
{
    void *session = field<void *>(field<void *>(processor, Proc::Decoder), Dec::Session);
    auto *cpu = field<uint8_t *>(field<void *>(session, Sess::Context), Tex::Cpu);
    memset(cpu, 0, field<uint32_t>(session, Sess::ContextSize));
    fillDefaultProbs(cpu);
}

//! getDecodeTargetArea: the visible frame.
extern "C" void vp9GetDecodeTargetArea(void *processor, void *, int32_t *rect) {
    const State *s = state(processor);
    rect[0] = rect[1] = 0;
    rect[2] = static_cast<int32_t>(s ? s->width : 0);
    rect[3] = static_cast<int32_t>(s ? s->height : 0);
}

//! SetBitstreamFields(P, source, destination, build): the frame for the decoder. In direct mode (config bit, as in
//! AvdHevcDecDataProcessor) the VCN reads AppleGVA's buffer in place at the frame's offset in it; otherwise the frame
//! is copied into the driver's bitstream ring.
extern "C" int vp9SetBitstreamFields(void *processor, void *params, void *source, void *destination, void *build) {
    void *decoder = field<void *>(processor, Proc::Decoder);
    const auto *frame = field<const uint8_t *>(params, Pic::Frame);
    const uint64_t size = field<uint64_t>(params, Pic::FrameSize);
    const bool direct = configFlags(processor, Cfg::Flags94) & Cfg::DirectBitstream;
    void *texture = direct ? source : destination;
    auto *cpu = texture ? field<uint8_t *>(texture, Tex::Cpu) : nullptr;
    // Direct: the frame must lie inside the source buffer (AppleGVA passes its start, offset 0).
    if (!state(processor) || !frame || size == 0 || size > field<uint32_t>(decoder, Dec::MaxBitstream) || !cpu ||
        (direct && frame < cpu)) {
        return 1;
    }
    void *session = field<void *>(decoder, Dec::Session);
    const auto offset = static_cast<uint32_t>(frame - cpu);
    if (direct) {
        cpu = const_cast<uint8_t *>(frame);
    } else {
        memcpy(cpu, frame, size);
    }
    field<uint8_t *>(session, Sess::Bitstream) = cpu;
    field<uint32_t>(session, Sess::BitstreamSize) = static_cast<uint32_t>(size);
    if (nrPrepareBitStream(processor, static_cast<uint8_t *>(decoder) + Dec::Decrypt)) {
        return 1;
    }
    field<uint32_t>(build, Build::BitstreamOn) = 1;
    field<void *>(build, Build::Bitstream) = texture;
    field<uint32_t>(build, Build::BitstreamOffset) = direct ? offset : 0;
    return 0;
}

//! SetDecodeMessageFields(P, target, build, 0, 0): the whole VCN decode message for one VP9 frame.
extern "C" int vp9SetDecodeMessageFields(void *processor, void *params, void *target, void *build, uint32_t,
                                         uint32_t) {
    State *s = state(processor);
    void *decoder = field<void *>(processor, Proc::Decoder);
    void *session = field<void *>(decoder, Dec::Session);
    field<uint8_t>(session, Sess::Dirty) = 0;
    if (!s || !target) {
        return 1;
    }

    const uint32_t pitchBytes = field<uint32_t>(target, Tex::Pitch), rows = field<uint32_t>(target, Tex::Height);
    const uint32_t bytesPerSample = field<uint32_t>(target, Tex::BytesPerElement);
    if (bytesPerSample - 1 > 1 || (s->bytesPerSample && s->bytesPerSample != bytesPerSample)) {
        return 1;
    }

    auto *msg = field<uint8_t *>(session, Sess::Message);
    memset(msg, 0, MessageSize);
    uint8_t *probBuffer = nrGetSegFgBuffer(decoder);
    auto *probs = reinterpret_cast<ProbsSegment *>(probBuffer);
    memset(probs, 0, sizeof(*probs));

    // The VP9 message (V2 port). Surfaces identify the AppleGVA buffers for the reference slot table.
    Surface refs[8];
    for (uint32_t i = 0; i < 8; i++) {
        refs[i] = field<Surface>(params, Pic::Refs + i * Pic::RefStride + Pic::Target);
    }
    Header h;
    const Status status = s->session.build(field<const uint8_t *>(session, Sess::Bitstream),
                                           field<uint32_t>(session, Sess::BitstreamSize),
                                           field<Surface>(params, Pic::Target), refs, bytesPerSample == 2,
                                           *reinterpret_cast<Message *>(msg + OffsetVp9), *probs, &h,
                                           bytesPerSample == 2 ? 10 : 8);
    if (status != Status::Ok || h.width > s->maxWidth || h.height > s->maxHeight ||
        rows < h.height || pitchBytes < h.width * bytesPerSample) {
        return 1;
    }
    s->bytesPerSample = bytesPerSample;
    const auto g = dpbGeometry(*s);
    const uint32_t dpbSize = DpbSlots * (g.lumaSize + g.chromaSize);
    const uint32_t ctxSize = vp9CalculateContextSize(processor);
    if (dpbSize > field<uint32_t>(session, Sess::DpbSize) || ctxSize > field<uint32_t>(session, Sess::ContextSize)) {
        field<uint8_t>(session, Sess::Dirty) = 1;
        field<uint32_t>(session, Sess::DpbSize) = dpbSize;
        field<uint32_t>(session, Sess::ContextSize) = ctxSize;
    }
    s->width = h.width, s->height = h.height;
    field<uint32_t>(decoder, Dec::Width) = h.width;
    field<uint32_t>(decoder, Dec::Height) = h.height;

    // Header and index, laid out like Mesa (decode, codec, dynamic DPB; no DRM).
    auto *header = reinterpret_cast<MessageHeader *>(msg);
    header->headerSize = 0x28;
    header->totalSize = MessageSize;
    header->numBuffers = 3;
    header->msgType = MsgTypeDecode;
    header->streamHandle = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(session));    // as in the create message
    header->feedbackNumber = field<uint32_t>(processor, Proc::FrameCounter);
    header->index[0] = {MessageDecode, OffsetDecode, sizeof(DecodeMessage), 0};
    header->index[1] = {MessageVp9, OffsetVp9, sizeof(Message), 0};
    header->index[2] = {MessageDynamicDpb, OffsetDpb, sizeof(DynamicDpb), 0};

    // Generic decode message: the decode target as the driver fills it for HEVC on VCN 2, the DPB as Mesa's Tier 1.
    auto *decode = reinterpret_cast<DecodeMessage *>(msg + OffsetDecode);
    decode->streamType = CodecVP9;
    decode->decodeFlags = FlagUseDynamicDpb | FlagUsePal;
    decode->width = h.width;
    decode->height = h.height;
    decode->bsdSize = field<uint32_t>(session, Sess::BitstreamSize);
    decode->dpbSize = field<uint32_t>(session, Sess::DpbSize);
    decode->dtSize = rows * pitchBytes * 3 / 2;
    decode->hwCtxtSize = field<uint32_t>(session, Sess::ContextSize);
    decode->dbPitch = g.pitch;
    decode->dbAlignedHeight = g.height;
    decode->dbPitchUv = g.pitch / 2;
    decode->dtPitch = pitchBytes >> (bytesPerSample - 1);
    decode->dtUvPitch = decode->dtPitch / 2;
    decode->dtLumaTopOffset = 0;
    decode->dtLumaBottomOffset = h.height * pitchBytes - 1;
    decode->dtChromaTopOffset = align(rows, 16) * pitchBytes;
    decode->dtChromaBottomOffset = decode->dtChromaTopOffset + h.height * pitchBytes / 2 - 1;

    auto *dpb = reinterpret_cast<DynamicDpb *>(msg + OffsetDpb);
    dpb->lumaPitch = g.pitch;
    dpb->lumaAlignedHeight = g.height;
    dpb->lumaAlignedSize = g.lumaSize;
    dpb->chromaPitch = g.pitch / 2;
    dpb->chromaAlignedHeight = g.height / 2;
    dpb->chromaAlignedSize = g.chromaSize;
    dpb->arraySize = DpbSlots;
    dpb->reserved0[0] = DpbAlignment;

    // Command buffers, as the AV1 path fills them (DPB and context are refreshed by VADecoder after allocation).
    field<uint32_t>(build, Build::MessageOn) = 1;
    field<void *>(build, Build::Message) =
        field<void *>(session, Sess::MessageTextures + 8 * field<uint32_t>(session, Sess::MessageIndex));
    field<uint32_t>(build, Build::DpbOn) = 1;
    field<void *>(build, Build::Dpb) = field<void *>(session, Sess::Dpb);
    field<uint32_t>(build, Build::TargetOn) = 1;
    field<void *>(build, Build::Target) = target;
    field<uint32_t>(build, Build::TargetOffset) = 0;
    field<uint32_t>(build, Build::ProbOn) = 1;
    field<void *>(build, Build::Prob) =
        field<void *>(session, Sess::ProbTextures + 8 * field<uint32_t>(session, Sess::ProbIndex));
    field<uint32_t>(build, Build::ContextOn) = 1;
    field<void *>(build, Build::Context) = field<void *>(session, Sess::Context);
    if (configFlags(processor, Cfg::Flags95) & Cfg::ItBuffer) {
        field<uint32_t>(build, Build::ItOn) = 1;
        field<void *>(build, Build::It) = field<void *>(session, Sess::ItTexture);
    }
    return 0;
}

// ---- AvdVAContext::DecodePicture, type 0x1000 ----------------------------------------------------------------------

namespace {

//! The bitstream pool texture that holds the frame: a check that P+0/P+8 really point into AppleGVA's buffers.
void *poolTexture(void *context, const uint8_t *frame, uint64_t size) {
    for (size_t pool = 0; pool < 2; pool++) {
        const size_t base = pool * Ctx::PoolStride;
        if (!field<uint32_t>(context, base + Ctx::PoolValid)) { continue; }
        const uint32_t bytes = field<uint32_t>(context, base + Ctx::PoolSize);
        const uint32_t count = field<uint32_t>(context, base + Ctx::PoolCount);
        for (uint32_t i = 0; i < count && i < 32; i++) {
            void *texture = field<void *>(context, (pool ? Ctx::Pool1 : Ctx::Pool0) + i * 8);
            if (!texture) { continue; }
            const auto *cpu = field<const uint8_t *>(texture, Tex::Cpu);
            if (cpu && frame >= cpu && size <= bytes && static_cast<uint64_t>(frame - cpu) <= bytes - size) {
                return texture;
            }
        }
    }
    return nullptr;
}

}    // namespace

//! Same steps as the AV1 branch: bitstream texture, output texture, VADecoder::DecodePicture, setRefCon.
//! Returns the value AvdVAContext::DecodePicture returns: 5 when the frame was not decoded.
extern "C" int vp9DecodePicture(void *context, void *command, void *refCon) {
    void *decoder = field<void *>(command, Cmd::Decoder);
    void *params = field<void *>(command, Cmd::Params);
    if (!decoder || !params) {
        return 5;
    }
    void *bitstream =
        poolTexture(context, field<const uint8_t *>(params, Pic::Frame), field<uint64_t>(params, Pic::FrameSize));
    void *surface = field<void *>(params, Pic::Target);
    if (!bitstream || !surface) {
        return 5;
    }
    void *va = field<void *>(context, Ctx::Va);
    void *output = nullptr;
    if (nrGetIOSurfaceBuffer(va, surface, &output, 0, nullptr, true)) {
        return 1;
    }
    using DecodeFn = int (*)(void *, void *, void *, void *);
    const int decoded =
        (*reinterpret_cast<DecodeFn *>(field<uint8_t *>(decoder, 0) + 0x48))(decoder, params, bitstream, output);
    // The AV1 branch ignores this result and completes the frame, whose surface the VCN never wrote. Fail it instead
    // with the branch's own 5 (no refCon then): VideoToolbox reports kVTVideoDecoderMalfunctionErr for it and later
    // frames, so the client recreates the session or falls back to software instead of showing corrupt frames.
    if (decoded) { return 5; }
    nrSetRefCon(va, refCon, field<void *>(decoder, 0xF0), command);
    return 0;
}

//! Entered by a jump from AvdVAContext::DecodePicture (rbx = command, r14 = refCon, r15 = context, stack aligned),
//! leaves through that function's epilogue with the result in r12d.
asm(R"(
    .text
    .globl _vp9DecodePictureEntry
_vp9DecodePictureEntry:
    movq %r15, %rdi
    movq %rbx, %rsi
    movq %r14, %rdx
    callq _vp9DecodePicture
    movl %eax, %r12d
    jmp _nrDecodePictureEpilogue
)");

//! Called instead of AvdCapability::getAVDFuncSubId from setupStaticCoreFunctions. Function 6 is the AV1 decoder
//! (kind 1, type 0x1000000), which VCN 2 cannot run; it is advertised as VP9 (type 0x1000) so that AppleGVA's VP9
//! capability lookup finds it.
asm(R"(
    .text
    .globl _vp9GetAVDFuncSubId
_vp9GetAVDFuncSubId:
    cmpl $6, %esi
    jne 1f
    movl $0x1000, %eax
    retq
1:  jmp _nrGetAVDFuncSubId
)");

//! 函数 6 为 AppleGVA 提供 NV12 和 x420，分别对应 VP9 的 8 位和 10 位输出。
asm(R"(
    .text
    .globl _vp9GetAVDFuncOutputFormatInfo
_vp9GetAVDFuncOutputFormatInfo:
    cmpl $6, %esi
    jne 1f
    movl $2, (%rdx)
    movabsq $0x14e563132, %rax
    movq %rax, (%rcx)
    movabsq $0x178343230, %rax
    movq %rax, 8(%rcx)
    xorl %eax, %eax
    retq
1:  jmp _nrGetAVDFuncOutputFormatInfo

    .section __TEXT,__nrhelpers
    .globl _vp9BufferRequirement
_vp9BufferRequirement:
    cmpl $0x78343230, 0x18(%rsi)
    jne 1f
    cmpl $0, 0xc(%rsi)
    je 1f
    movl $3, %eax
    movl $0x78343230, %ecx
    jmp _nrBufferRequirementP010
1:  jmp _nrBufferRequirementNV12
)");
