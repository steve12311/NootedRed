// Harness for tools/test_vp9_backend.py: runs the linked VP9 backend blob (the bytes that go into the AMD driver)
// against fake driver structures and checks each VCN message against VP9Message compiled natively (equal to Mesa,
// tools/test_vp9_message.py). The test links the blob at BlobBase; driver calls go through trampolines to the stubs.
#include <VP9DefaultProbs.hpp>
#include <VP9Message.hpp>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sys/mman.h>
#include <utility>
#include <vector>

#include "vp9-backend-symbols.h"    // BlobBase, BlobSize, TableOffset, Sym* offsets, StateSize (generated)

using namespace VP9Message;

static int failures = 0;
#define CHECK(cond, ...)                                                                                              \
    do {                                                                                                              \
        if (!(cond)) {                                                                                                \
            if (failures++ < 20) {                                                                                    \
                fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__);                                                  \
                fprintf(stderr, __VA_ARGS__);                                                                         \
                fprintf(stderr, "\n");                                                                                \
            }                                                                                                         \
        }                                                                                                             \
    } while (0)

template<typename T>
static T &at(void *base, size_t offset) {
    return *reinterpret_cast<T *>(static_cast<uint8_t *>(base) + offset);
}
static void *zalloc(size_t size) { return calloc(1, size); }

// ---- Fake driver ---------------------------------------------------------------------------------------------------

struct Texture {
    uint8_t raw[0x100];
};
static Texture *texture(size_t bytes, uint32_t rows = 0, uint32_t pitch = 0, uint32_t bpe = 1) {
    auto *t = static_cast<Texture *>(zalloc(sizeof(Texture)));
    at<uint32_t>(t, 0x7C) = rows;
    at<uint32_t>(t, 0x80) = pitch;
    at<uint32_t>(t, 0x84) = bpe;
    at<uint8_t *>(t, 0x98) = static_cast<uint8_t *>(zalloc(bytes ? bytes : 1));
    return t;
}

static int createCalls, prepareCalls;
static int stubCreate(void *, void *) { return createCalls++, 0; }
static int stubPrepare(void *, void *) { return prepareCalls++, 0; }
static uint8_t *stubSegFg(void *decoder) {
    void *session = at<void *>(decoder, 0x58);
    auto &index = at<uint32_t>(session, 0x270);
    index = (index + 1) & 0xF;
    return at<uint8_t *>(at<void *>(session, 0x1F0 + index * 8), 0x98);
}
static void *lastSurface, *outputTexture;
static int stubGetSurface(void *, void *surface, void **tex, uint32_t plane, uint32_t *offset, bool wait) {
    lastSurface = surface;
    CHECK(plane == 0 && offset == nullptr && wait, "getIOSurfaceBuffer arguments");
    *tex = outputTexture;
    return 0;
}
static void *refConArgs[4];
static void stubSetRefCon(void *va, void *refCon, void *tex, void *cmd) {
    refConArgs[0] = va, refConArgs[1] = refCon, refConArgs[2] = tex, refConArgs[3] = cmd;
}
static void *decodeArgs[4];
static int decodeCalls, decodeResult;
static int stubDecoderDecode(void *decoder, void *params, void *bitstream, void *output) {
    decodeArgs[0] = decoder, decodeArgs[1] = params, decodeArgs[2] = bitstream, decodeArgs[3] = output;
    return decodeCalls++, decodeResult;
}

struct Driver {
    void *processor, *decoder, *session, *config, *configHolder, *messageTexture, *dpb, *context, *vtable[16];
    std::vector<uint8_t> params;
};

static Driver makeDriver(uint32_t width, uint32_t height) {
    Driver d {};
    d.processor = zalloc(0x48 + StateSize);
    memset(static_cast<uint8_t *>(d.processor) + 0x48, 0xA5, StateSize);    // operator new does not clear
    d.decoder = zalloc(0x200);
    d.session = zalloc(0x300);
    d.config = zalloc(0x100);
    d.configHolder = zalloc(0x500);
    at<void *>(d.configHolder, 0x460) = d.config;
    at<void *>(d.processor, 0x10) = d.decoder;
    at<void *>(d.processor, 0x18) = d.configHolder;
    at<void *>(d.decoder, 0x58) = d.session;
    at<uint32_t>(d.decoder, 0x7C) = width;
    at<uint32_t>(d.decoder, 0x80) = height;
    at<uint32_t>(d.decoder, 0x8C) = 4 << 20;
    d.vtable[9] = reinterpret_cast<void *>(stubDecoderDecode);    // +0x48
    at<void *>(d.decoder, 0) = d.vtable;
    at<void *>(d.decoder, 0xF0) = reinterpret_cast<void *>(0xF0F0);
    d.messageTexture = texture(0x1008);
    at<void *>(d.session, 0x98 + 3 * 8) = d.messageTexture;
    at<uint32_t>(d.session, 0xD8) = 3;
    at<uint8_t *>(d.session, 0xE0) = at<uint8_t *>(d.messageTexture, 0x98);
    for (int i = 0; i < 16; i++) { at<void *>(d.session, 0x1F0 + i * 8) = texture(0x5400); }
    d.params.assign(0xA600, 0);
    return d;
}

// ---- Blob ----------------------------------------------------------------------------------------------------------

template<typename F>
static F fn(size_t offset) {
    return reinterpret_cast<F>(BlobBase + offset);
}
using CreateFn = int (*)(void *, void *);
using SizeFn = uint32_t (*)(void *);
using InitFn = void (*)(void *);
using AreaFn = void (*)(void *, void *, int32_t *);
using BitstreamFn = int (*)(void *, void *, void *, void *, void *);
using MessageFn = int (*)(void *, void *, void *, void *, uint32_t, uint32_t);
using DecodeFn = int (*)(void *, void *, void *);

static void loadBlob(const char *path) {
    void *where = mmap(reinterpret_cast<void *>(BlobBase), BlobSize, PROT_READ | PROT_WRITE | PROT_EXEC,
                       MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0);
    if (where != reinterpret_cast<void *>(BlobBase)) {
        perror("mmap");
        exit(2);
    }
    FILE *f = fopen(path, "rb");
    if (!f || fread(where, 1, BlobSize, f) != BlobSize) {
        fprintf(stderr, "cannot read %s\n", path);
        exit(2);
    }
    fclose(f);
    void *table[] = {reinterpret_cast<void *>(stubCreate), reinterpret_cast<void *>(stubPrepare),
                     reinterpret_cast<void *>(stubSegFg), reinterpret_cast<void *>(stubGetSurface),
                     reinterpret_cast<void *>(stubSetRefCon), nullptr /* DecodePicture epilogue: not called */};
    memcpy(reinterpret_cast<uint8_t *>(BlobBase) + TableOffset, table, sizeof(table));
}

// ---- IVF and superframes -------------------------------------------------------------------------------------------

static std::vector<std::vector<uint8_t>> frames(const char *path) {
    std::vector<std::vector<uint8_t>> out;
    FILE *f = fopen(path, "rb");
    if (!f) { return out; }
    std::vector<uint8_t> file;
    uint8_t buf[65536];
    for (size_t n; (n = fread(buf, 1, sizeof(buf), f));) { file.insert(file.end(), buf, buf + n); }
    fclose(f);
    size_t pos = file[6] | file[7] << 8;
    while (pos + 12 <= file.size()) {
        uint32_t size = file[pos] | file[pos + 1] << 8 | file[pos + 2] << 16 | uint32_t(file[pos + 3]) << 24;
        const uint8_t *p = file.data() + pos + 12;
        pos += 12 + size;
        uint8_t marker = p[size - 1];
        if ((marker & 0xE0) == 0xC0) {
            uint32_t count = (marker & 7) + 1, bytes = ((marker >> 3) & 3) + 1, index = 2 + bytes * count;
            if (size >= index && p[size - index] == marker) {
                const uint8_t *q = p + size - index + 1;
                size_t offset = 0;
                for (uint32_t i = 0; i < count; i++) {
                    uint32_t len = 0;
                    for (uint32_t b = 0; b < bytes; b++) { len |= uint32_t(*q++) << (8 * b); }
                    out.emplace_back(p + offset, p + offset + len);
                    offset += len;
                }
                continue;
            }
        }
        out.emplace_back(p, p + size);
    }
    return out;
}

// ---- Tests ---------------------------------------------------------------------------------------------------------

static void testClip(const char *path, uint32_t width, uint32_t height, bool expectDecode) {
    auto list = frames(path);
    CHECK(!list.empty(), "no frames in %s", path);
    Driver d = makeDriver(width, height);
    uint8_t build[0x100];
    CHECK(fn<CreateFn>(SymCreate)(d.processor, build) == 0 && createCalls > 0, "create");
    const uint32_t pitch = (width + 255) & ~255u, rows = (height + 15) & ~15u;
    Texture *target = texture(pitch * rows * 3 / 2, rows, pitch, 1);
    Texture *ring = texture(4 << 20);
    Session reference;
    Surface slots[8] {};
    uint32_t decoded = 0;
    for (size_t n = 0; n < list.size(); n++) {
        auto &frame = list[n];
        Surface target_id = 1;
        while ([&] {
            for (auto s : slots) {
                if (s == target_id) { return true; }
            }
            return false;
        }()) {
            target_id++;
        }
        uint8_t *P = d.params.data();
        memset(P, 0, d.params.size());
        at<const uint8_t *>(P, 0) = frame.data();
        at<uint64_t>(P, 8) = frame.size();
        at<Surface>(P, 0x1250) = target_id;
        for (int i = 0; i < 8; i++) { at<Surface>(P, 0x1260 + i * 0x1258 + 0x1250) = slots[i]; }
        memset(build, 0, sizeof(build));

        Message expected {};
        ProbsSegment expectedProbs {};
        Header h;
        const Status want = reference.build(frame.data(), frame.size(), target_id, slots, false, expected,
                                            expectedProbs, &h);
        const bool ok = want == Status::Ok && h.bitDepth == 8;

        int rc = fn<BitstreamFn>(SymBitstream)(d.processor, P, nullptr, ring, build);
        CHECK(rc == 0, "%s frame %zu: SetBitstreamFields %d", path, n, rc);
        CHECK(!memcmp(at<uint8_t *>(ring, 0x98), frame.data(), frame.size()), "bitstream copy");
        CHECK(at<void *>(build, 0x28) == ring && at<uint32_t>(build, 0x20) == 1, "bitstream build info");
        rc = fn<MessageFn>(SymMessage)(d.processor, P, target, build, 0, 0);
        CHECK((rc == 0) == ok, "%s frame %zu: SetDecodeMessageFields %d, expected ok=%d", path, n, rc, ok);
        if (rc == 0 && ok) {
            decoded++;
            const uint8_t *msg = at<uint8_t *>(d.session, 0xE0);
            // VP9 codec message and probability/segment buffer: identical to the native port.
            CHECK(!memcmp(msg + 0x178, &expected, sizeof(expected)), "%s frame %zu: VP9 message differs", path, n);
            const uint8_t *probs = at<uint8_t *>(at<void *>(d.session, 0x1F0 + at<uint32_t>(d.session, 0x270) * 8),
                                                 0x98);
            CHECK(!memcmp(probs, &expectedProbs, sizeof(expectedProbs)), "%s frame %zu: probs differ", path, n);
            // Header and index (Mesa layout: decode, codec, dynamic DPB).
            const uint32_t *hd = reinterpret_cast<const uint32_t *>(msg);
            const uint32_t header[] = {0x28,  0x1F0, 3, 1, uint32_t(uintptr_t(d.session)), at<uint32_t>(d.processor, 0x20),
                                       2,     0x48,  0xBC, 0, 0xE, 0x178, 0x78, 0, 0x10, 0x104, 0x74, 0};
            CHECK(!memcmp(hd, header, sizeof(header)), "%s frame %zu: header", path, n);
            // Decode message.
            const uint32_t *dm = reinterpret_cast<const uint32_t *>(msg + 0x48);
            const uint32_t dp = (width + 63) & ~63u, dh = (height + 63) & ~63u;
            CHECK(dm[0] == 0x11 && dm[1] == 9 && dm[2] == h.width && dm[3] == h.height, "decode: type/flags/size");
            CHECK(dm[4] == frame.size() && dm[5] == 9 * dp * dh * 3 / 2 && dm[6] == rows * pitch * 3 / 2 &&
                      dm[9] == contextSize(8),
                  "decode: buffer sizes %u %u %u %u", dm[4], dm[5], dm[6], dm[9]);
            CHECK(dm[0x48 / 4] == dp && dm[0x4C / 4] == dh && dm[0xA4 / 4] == dp / 2, "decode: DPB pitch/height");
            CHECK(dm[0x64 / 4] == pitch && dm[0x68 / 4] == pitch / 2, "decode: target pitch");
            CHECK(dm[0x88 / 4] == 0 && dm[0x8C / 4] == h.height * pitch - 1 && dm[0x90 / 4] == rows * pitch &&
                      dm[0x94 / 4] == rows * pitch + h.height * pitch / 2 - 1,
                  "decode: target offsets");
            for (uint32_t off : {0x1Cu, 0x20u, 0x28u, 0x2Cu, 0x30u, 0x44u, 0x50u, 0x54u, 0x58u, 0x6Cu, 0x70u, 0x74u,
                                 0xA0u}) {
                CHECK(dm[off / 4] == 0, "decode field %#x not zero", off);
            }
            // Dynamic DPB (Tier 1).
            const uint32_t *dd = reinterpret_cast<const uint32_t *>(msg + 0x104);
            CHECK(dd[0] == 0 && dd[1] == dp && dd[2] == dh && dd[3] == dp * dh && dd[4] == dp / 2 && dd[5] == dh / 2 &&
                      dd[6] == dp * dh / 2 && msg[0x104 + 28] == 9 && msg[0x104 + 46] == 64,
                  "dynamic DPB");
            // Build info.
            CHECK(at<void *>(build, 0x08) == d.messageTexture && at<void *>(build, 0x78) == target &&
                      at<uint32_t>(build, 0x70) == 1 && at<uint32_t>(build, 0x90) == 1 &&
                      at<void *>(build, 0x98) == at<void *>(d.session, 0x1F0 + at<uint32_t>(d.session, 0x270) * 8) &&
                      at<uint32_t>(build, 0xA0) == 1 && at<uint32_t>(build, 0xB0) == 0,
                  "build info");
            // Allocation, as VADecoder does after Process.
            if (at<uint8_t>(d.session, 0x0C)) {
                CHECK(at<uint32_t>(d.session, 0x30) == 9 * dp * dh * 3 / 2 && at<uint32_t>(d.session, 0x1C8) ==
                          contextSize(8), "sizes on allocation");
                at<void *>(d.session, 0x1D0) = texture(at<uint32_t>(d.session, 0x1C8));
                memset(at<uint8_t *>(at<void *>(d.session, 0x1D0), 0x98), 0x5A, at<uint32_t>(d.session, 0x1C8));
                fn<InitFn>(SymInit)(d.processor);
                const uint8_t *ctx = at<uint8_t *>(at<void *>(d.session, 0x1D0), 0x98);
                CHECK(!memcmp(ctx, DefaultProbs, sizeof(DefaultProbs)) && ctx[sizeof(DefaultProbs)] == 0 &&
                          ctx[contextSize(8) - 1] == 0,
                      "context initialisation");
            }
            int32_t rect[4];
            fn<AreaFn>(SymArea)(d.processor, P, rect);
            CHECK(rect[0] == 0 && rect[1] == 0 && rect[2] == h.width && rect[3] == h.height, "target area");
        }
        at<uint32_t>(d.processor, 0x20)++;
        if (want == Status::Ok) {
            for (int i = 0; i < 8; i++) {
                if (h.refreshFrameFlags >> i & 1) { slots[i] = target_id; }
            }
        }
    }
    CHECK(fn<SizeFn>(SymDpb)(d.processor) == 9 * ((width + 63) & ~63u) * ((height + 63) & ~63u) * 3 / 2, "DPB size");
    CHECK(fn<SizeFn>(SymContext)(d.processor) == contextSize(8), "context size");
    CHECK(expectDecode ? decoded > 0 : decoded == 0, "%s: %u frames decoded", path, decoded);
    printf("%-40s %3zu frames, %3u messages checked\n", strrchr(path, '/') + 1, list.size(), decoded);
}

static void testGuards() {
    Driver d = makeDriver(352, 288);
    uint8_t build[0x100] {};
    uint8_t frame[64] {0x82, 0x49, 0x83, 0x42};
    uint8_t *P = d.params.data();
    at<const uint8_t *>(P, 0) = frame;
    at<uint64_t>(P, 8) = sizeof(frame);
    Texture *ring = texture(4096);
    // No state yet (operator new garbage): everything refuses.
    CHECK(fn<BitstreamFn>(SymBitstream)(d.processor, P, nullptr, ring, build) == 1, "bitstream without state");
    CHECK(fn<SizeFn>(SymDpb)(d.processor) == 0, "DPB without state");
    fn<CreateFn>(SymCreate)(d.processor, build);
    at<uint64_t>(P, 8) = (4 << 20) + 1;
    CHECK(fn<BitstreamFn>(SymBitstream)(d.processor, P, nullptr, ring, build) == 1, "oversized frame");
    at<uint64_t>(P, 8) = 0;
    CHECK(fn<BitstreamFn>(SymBitstream)(d.processor, P, nullptr, ring, build) == 1, "empty frame");
    at<uint64_t>(P, 8) = sizeof(frame);
    at<uint8_t>(d.config, 0x94) = 0x80;
    CHECK(fn<BitstreamFn>(SymBitstream)(d.processor, P, nullptr, ring, build) == 1, "direct bitstream mode");
    at<uint8_t>(d.config, 0x94) = 0;
    CHECK(fn<MessageFn>(SymMessage)(d.processor, P, nullptr, build, 0, 0) == 1, "no target");
    // Sessions larger than 4096 are refused before the driver's create message.
    const int creates = createCalls;
    for (const auto [w, h] : {std::pair {8192u, 4320u}, {4352u, 2448u}, {1920u, 4100u}}) {
        Driver big = makeDriver(w, h);
        CHECK(fn<CreateFn>(SymCreate)(big.processor, build) != 0 && createCalls == creates, "%ux%u create refused", w,
              h);
        CHECK(fn<SizeFn>(SymDpb)(big.processor) == 0, "%ux%u session has no state", w, h);
    }
    Driver dci = makeDriver(4096, 2160);
    CHECK(fn<CreateFn>(SymCreate)(dci.processor, build) == 0 && fn<SizeFn>(SymDpb)(dci.processor) > 0,
          "4096x2160 accepted");

    // AvdVAContext::DecodePicture branch.
    void *context = zalloc(0x400);
    at<void *>(context, 0x08) = reinterpret_cast<void *>(0x7A7A);
    Texture *pool = texture(0x10000);
    at<uint32_t>(context, 0x130) = 1;      // pool 1 valid
    at<uint32_t>(context, 0x138) = 0x10000;
    at<uint32_t>(context, 0x13C) = 4;
    at<void *>(context, 0x140 + 2 * 8) = pool;
    uint8_t command[0x40] {};
    at<void *>(command, 0x18) = d.decoder;
    at<void *>(command, 0x20) = P;
    at<const uint8_t *>(P, 0) = at<uint8_t *>(pool, 0x98) + 0x800;
    at<uint64_t>(P, 8) = 0x100;
    at<void *>(P, 0x1250) = reinterpret_cast<void *>(0x5A5);
    outputTexture = texture(16);
    int rc = fn<DecodeFn>(SymDecode)(context, command, reinterpret_cast<void *>(0xCAFE));
    CHECK(rc == 0 && decodeCalls == 1 && decodeArgs[0] == d.decoder && decodeArgs[1] == P && decodeArgs[2] == pool &&
              decodeArgs[3] == outputTexture && lastSurface == reinterpret_cast<void *>(0x5A5),
          "DecodePicture: decoder call");
    CHECK(refConArgs[0] == reinterpret_cast<void *>(0x7A7A) && refConArgs[1] == reinterpret_cast<void *>(0xCAFE) &&
              refConArgs[2] == reinterpret_cast<void *>(0xF0F0) && refConArgs[3] == command,
          "DecodePicture: setRefCon");
    at<uint64_t>(P, 8) = 0x10000 - 0x800 + 1;    // runs past the pool buffer
    CHECK(fn<DecodeFn>(SymDecode)(context, command, nullptr) == 5 && decodeCalls == 1, "frame outside pool");
    at<uint64_t>(P, 8) = 0x100;
    at<void *>(P, 0x1250) = nullptr;
    CHECK(fn<DecodeFn>(SymDecode)(context, command, nullptr) == 5 && decodeCalls == 1, "no output surface");
    // A frame the decoder failed is failed, not completed (its surface was never written).
    at<void *>(P, 0x1250) = reinterpret_cast<void *>(0x5A5);
    refConArgs[1] = nullptr;
    decodeResult = 3;
    CHECK(fn<DecodeFn>(SymDecode)(context, command, reinterpret_cast<void *>(0xBEEF)) == 5 && decodeCalls == 2 &&
              refConArgs[1] == nullptr,
          "decoder failure fails the frame");
    decodeResult = 0;
    printf("guards and DecodePicture branch checked\n");
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: harness blob.bin [clip.ivf width height expect]...\n");
        return 2;
    }
    loadBlob(argv[1]);
    testGuards();
    for (int i = 2; i + 3 < argc + 1 && i + 3 <= argc; i += 4) {
        testClip(argv[i], uint32_t(atoi(argv[i + 1])), uint32_t(atoi(argv[i + 2])), atoi(argv[i + 3]) != 0);
    }
    printf(failures ? "FAIL (%d)\n" : "PASS\n", failures);
    return failures ? 1 : 0;
}
