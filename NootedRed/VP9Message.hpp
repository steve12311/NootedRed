//! VP9 plan, phase V2: turns one VP9 frame into the VCN 2 decode message, without the kernel or the AMD driver.
//!
//! Input is what AppleGVA hands the vendor driver with `kAVDPictureDecodeVP9` (one frame of the bitstream, already
//! split from its superframe, plus the output surface and the eight `ref_frame_map` surfaces). The uncompressed
//! header is read from the bitstream itself, like Mesa's VA frontend does, so the message does not depend on the
//! parts of AppleGVA's parameter block that are still unconfirmed.
//!
//! Ported from Mesa (src/gallium/frontends/va/picture_vp9.c, src/gallium/drivers/radeonsi/mm/si_video_dec.c,
//! src/amd/common/ac_vcn_dec.c and ac_vcn_dec.h at commit 767fbf7c6492f64549c90e9a080ab5417895a9ec):
//!
//! Copyright 2017, 2018, 2026 Advanced Micro Devices, Inc. All Rights Reserved. SPDX-License-Identifier: MIT
//! Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
//! documentation files (the "Software"), to deal in the Software without restriction, including without limitation
//! the rights to use, copy, modify, merge, publish, distribute, sub license, and/or sell copies of the Software, and
//! to permit persons to whom the Software is furnished to do so, subject to the following conditions:
//! The above copyright notice and this permission notice (including the next paragraph) shall be included in all
//! copies or substantial portions of the Software.
//! THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
//! THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE
//! COPYRIGHT HOLDER(S) OR AUTHOR(S) BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
//! CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
//! IN THE SOFTWARE.

#pragma once
#include <stddef.h>
#include <stdint.h>

namespace VP9Message {

// ---- Firmware interface (ac_vcn_dec.h) -----------------------------------------------------------------------------

constexpr uint32_t CodecVP9 = 0x11;          // RDECODE_CODEC_VP9
constexpr uint32_t MessageVP9 = 0xE;         // RDECODE_MESSAGE_VP9
constexpr uint32_t ProbsDataSize = 2304;     // RDECODE_VP9_PROBS_DATA_SIZE
constexpr uint8_t DitheringRandom = 5;       // RDECODE_DITHERING_RANDOM
constexpr uint8_t NoSlot = 0x7F;

namespace HeaderFlag {
constexpr uint32_t FrameType = 1u << 1, ErrorResilient = 1u << 2, IntraOnly = 1u << 3, HighPrecisionMv = 1u << 4,
                   RefreshFrameContext = 1u << 5, FrameParallel = 1u << 6, SegmentationEnabled = 1u << 7,
                   SegmentationUpdateMap = 1u << 8, SegmentationTemporalUpdate = 1u << 9,
                   SegmentationUpdateData = 1u << 10, ModeRefDeltaEnabled = 1u << 11,
                   ModeRefDeltaUpdate = 1u << 12, UsePrevFrameMvs = 1u << 13, UseUncompressedHeader = 1u << 14,
                   FrameSizeAsOffset = 1u << 15;
}

struct Message {    // rvcn_dec_message_vp9_t
    uint32_t frameHeaderFlags;
    uint8_t frameContextIdx, resetFrameContext, currPicIdx, interpFilter, filterLevel, sharpnessLevel;
    uint8_t lfAdjLevel[8][4][2];
    uint8_t baseQIndex;
    int8_t yDcDeltaQ, uvAcDeltaQ, uvDcDeltaQ;
    uint8_t log2TileCols, log2TileRows, txMode, referenceMode, chromaFormat;
    uint8_t refFrameMap[8];
    uint8_t frameRefs[3], refFrameSignBias[3];
    uint8_t frameToShow, bitDepthLumaMinus8, bitDepthChromaMinus8, p010Mode, msbMode, luma10to8, chroma10to8;
    uint32_t vp9FrameSize, compressedHeaderSize, uncompressedHeaderSize;
    uint8_t reserved[2];
};
static_assert(sizeof(Message) == 120);

struct Probs {    // rvcn_dec_vp9_probs_t
    struct {
        uint16_t classesMask[2], bitsMask[2];
        uint8_t joints, sign[2], class0[2], class0Fp[2], fp[2], class0Hp[2], hp[2], reserve[11];
    } nmvcMask;
    uint8_t coefProbs[4][2][2][6][6][3];
    uint8_t yModeProb[4][9], uvModeProb[10][9], singleRefProb[5][2], switchableInterpProb[4][2];
    uint8_t partitionProb[16][3], interModeProbs[7][3], mbskipProbs[3], intraInterProb[4], compInterProb[5];
    uint8_t compRefProb[5], txProbs32x32[2][3], txProbs16x16[2][2], txProbs8x8[2][1], mvJoints[3];
    struct {
        uint8_t sign, classes[10], class0[1], bits[10], class0Fp[2][3], fp[3], class0Hp, hp;
    } mvComps[2];
};
static_assert(sizeof(Probs) == 2072);

struct ProbsSegment {    // rvcn_dec_vp9_probs_segment_t: the per-frame "IT" buffer
    union {
        Probs probs;
        uint8_t probsData[ProbsDataSize];
    };
    union {
        struct {
            uint32_t featureData[8];
            uint8_t treeProbs[7], predProbs[3], absDelta, featureMask[8];
        } seg;
        uint8_t segmentData[256];
    };
};
static_assert(sizeof(ProbsSegment) == ProbsDataSize + 256);

//! Hardware context size for a VCN 2.x session (calc_ctx_size_vp9).
constexpr uint32_t contextSize(uint32_t maxBitDepth) {
    uint32_t size = 2304 * 5 + 32 * 2 * 128 * 68 + 9 * 64 * 2 * 128 * 68 + 8 * 2 * 2 * 8192;
    return maxBitDepth == 10 ? size + 8 * 2 * 4096 : size;
}

// ---- Uncompressed header -------------------------------------------------------------------------------------------

enum class Status : uint8_t {
    Ok,
    ShowExisting,     //! nothing to decode: the frame repeats a reference
    Unsupported,      //! profile 1/3, 12 bit, or 4:4:4
    Malformed,        //! bad marker, sync code or truncated header
};

struct Header {
    uint8_t profile, bitDepth, frameType, showFrame, errorResilient, intraOnly, resetFrameContext;
    uint8_t refreshFrameFlags, refFrameIdx[3], refFrameSignBias[4], allowHighPrecisionMv, interpFilter;
    uint8_t refreshFrameContext, frameParallel, frameContextIdx, filterLevel, sharpness;
    uint8_t modeRefDeltaEnabled, modeRefDeltaUpdate, baseQIndex;
    int8_t yDcDeltaQ, uvDcDeltaQ, uvAcDeltaQ;
    uint8_t segEnabled, segUpdateMap, segTemporalUpdate, segUpdateData;
    uint8_t log2TileCols, log2TileRows;
    uint16_t width, height, compressedHeaderSize;
    uint32_t uncompressedHeaderSize;
};

class BitReader {
    const uint8_t *data;
    size_t size, pos {0};

    public:
    BitReader(const uint8_t *data, size_t size) : data(data), size(size) {}
    bool overrun() const { return pos > size * 8; }
    size_t bytesUsed() const { return (pos + 7) / 8; }
    // Out of line: the parser reads ~60 fields, and the AV1 code space this runs from in V3 is ~10 KB.
    __attribute__((noinline)) uint32_t u(unsigned n) {
        uint32_t v = 0;
        for (; n; n--, pos++) { v = (v << 1) | (pos < size * 8 ? (data[pos >> 3] >> (7 - (pos & 7))) & 1 : 0); }
        return v;
    }
    //! Sign-magnitude value as in the VP9 header (`su(n)`).
    __attribute__((noinline)) int32_t s(unsigned n) {
        int32_t v = static_cast<int32_t>(u(n));
        return u(1) ? -v : v;
    }
};

// ---- Session -------------------------------------------------------------------------------------------------------

//! Opaque surface handle (the `IOSurfaceRef` AppleGVA passes); 0 means "no surface".
using Surface = uint64_t;

//! State that VP9 carries from frame to frame, plus the slot table of the VCN reference list.
class Session {
    // Loop filter deltas and segmentation data persist until the bitstream changes them (Mesa's VA frontend).
    int8_t refDeltas[4] {1, 0, -1, -1}, modeDeltas[2] {};
    uint8_t absDelta {0}, treeProbs[7] {}, predProbs[3] {};
    struct SegmentFeatures
    {
        bool    altQEnabled[8], altLfEnabled[8], refEnabled[8], skipEnabled[8];
        int16_t altQ[8], altLf[8];
        uint8_t refValue[8];
    } features{};
    uint16_t slotWidth[8] {}, slotHeight[8] {};
    uint16_t prevWidth {0}, prevHeight {0};
    bool prevShowFrame {false};
    uint8_t bitDepth {8};
    Surface renderPicList[9] {};

    void colorConfig(BitReader &br, Header &h) {
        h.bitDepth = static_cast<uint8_t>(h.profile >= 2 ? (br.u(1) ? 12 : 10) : 8);
        if (br.u(3) != 7) {    // color_space != CS_RGB
            br.u(1);           // color_range
            if (h.profile == 1 || h.profile == 3) { br.u(3); }
        } else if (h.profile == 1 || h.profile == 3) {
            br.u(1);
        }
    }

    static void frameSize(BitReader &br, Header &h) {
        h.width = static_cast<uint16_t>(br.u(16) + 1);
        h.height = static_cast<uint16_t>(br.u(16) + 1);
    }

    static void renderSize(BitReader &br) {
        if (br.u(1)) { br.u(32); }
    }

    Status parse(const uint8_t *data, size_t size, Header &h, uint8_t expectedBitDepth) {
        BitReader br(data, size);
        h = {};
        if (br.u(2) != 2) { return Status::Malformed; }
        h.profile = static_cast<uint8_t>(br.u(1) | br.u(1) << 1);
        if (h.profile == 3) { br.u(1); }
        if (br.u(1)) { return Status::ShowExisting; }
        if (h.profile != 0 && h.profile != 2) { return Status::Unsupported; }
        h.frameType = static_cast<uint8_t>(br.u(1));
        h.showFrame = static_cast<uint8_t>(br.u(1));
        h.errorResilient = static_cast<uint8_t>(br.u(1));
        h.bitDepth = h.profile == 0 ? 8 : bitDepth;
        if (h.frameType == 0) {
            if (br.u(24) != 0x498342) { return Status::Malformed; }
            colorConfig(br, h);
            frameSize(br, h);
            renderSize(br);
            h.refreshFrameFlags = 0xFF;
        } else {
            h.intraOnly = h.showFrame ? 0 : static_cast<uint8_t>(br.u(1));
            h.resetFrameContext = h.errorResilient ? 0 : static_cast<uint8_t>(br.u(2));
            if (h.intraOnly) {
                if (br.u(24) != 0x498342) { return Status::Malformed; }
                if (h.profile > 0) { colorConfig(br, h); }
                h.refreshFrameFlags = static_cast<uint8_t>(br.u(8));
                frameSize(br, h);
                renderSize(br);
            } else {
                h.refreshFrameFlags = static_cast<uint8_t>(br.u(8));
                for (int i = 0; i < 3; i++) {
                    h.refFrameIdx[i] = static_cast<uint8_t>(br.u(3));
                    h.refFrameSignBias[i + 1] = static_cast<uint8_t>(br.u(1));
                }
                bool found = false;
                for (int i = 0; i < 3 && !found; i++) {
                    if ((found = br.u(1))) {
                        h.width = slotWidth[h.refFrameIdx[i]];
                        h.height = slotHeight[h.refFrameIdx[i]];
                    }
                }
                if (!found) { frameSize(br, h); }
                renderSize(br);
                h.allowHighPrecisionMv = static_cast<uint8_t>(br.u(1));
                // literal_to_type: the order used by libvpx, VA-API and the VCN firmware.
                static constexpr uint8_t literalToType[4] = {1, 0, 2, 3};
                h.interpFilter = br.u(1) ? 4 : literalToType[br.u(2)];
            }
        }
        if (h.bitDepth != (h.profile == 2 ? 10 : 8) || (expectedBitDepth && h.bitDepth != expectedBitDepth)) {
            return Status::Unsupported;
        }
        if (!h.errorResilient) {
            h.refreshFrameContext = static_cast<uint8_t>(br.u(1));
            h.frameParallel = static_cast<uint8_t>(br.u(1));
        } else {
            h.frameParallel = 1;
        }
        h.frameContextIdx = static_cast<uint8_t>(br.u(2));

        h.filterLevel = static_cast<uint8_t>(br.u(6));
        h.sharpness = static_cast<uint8_t>(br.u(3));
        if (h.frameType == 0 || h.intraOnly || h.errorResilient) {
            refDeltas[0] = 1, refDeltas[1] = 0, refDeltas[2] = -1, refDeltas[3] = -1;
            modeDeltas[0] = modeDeltas[1] = 0;
            absDelta                      = 0;
            features                      = {};
        }
        if ((h.modeRefDeltaEnabled = static_cast<uint8_t>(br.u(1))) &&
            (h.modeRefDeltaUpdate = static_cast<uint8_t>(br.u(1)))) {
            for (auto &d : refDeltas) {
                if (br.u(1)) { d = static_cast<int8_t>(br.s(6)); }
            }
            for (auto &d : modeDeltas) {
                if (br.u(1)) { d = static_cast<int8_t>(br.s(6)); }
            }
        }

        h.baseQIndex = static_cast<uint8_t>(br.u(8));
        h.yDcDeltaQ = static_cast<int8_t>(br.u(1) ? br.s(4) : 0);
        h.uvDcDeltaQ = static_cast<int8_t>(br.u(1) ? br.s(4) : 0);
        h.uvAcDeltaQ = static_cast<int8_t>(br.u(1) ? br.s(4) : 0);

        if ((h.segEnabled = static_cast<uint8_t>(br.u(1)))) {
            if ((h.segUpdateMap = static_cast<uint8_t>(br.u(1)))) {
                for (auto &p : treeProbs) { p = static_cast<uint8_t>(br.u(1) ? br.u(8) : 255); }
                h.segTemporalUpdate = static_cast<uint8_t>(br.u(1));
                for (auto &p : predProbs) { p = static_cast<uint8_t>(h.segTemporalUpdate && br.u(1) ? br.u(8) : 255); }
            }
            if ((h.segUpdateData = static_cast<uint8_t>(br.u(1)))) {
                absDelta = static_cast<uint8_t>(br.u(1));
                for (int i = 0; i < 8; i++) {
                    if ((features.altQEnabled[i] = br.u(1))) { features.altQ[i] = static_cast<int16_t>(br.s(8)); }
                    if ((features.altLfEnabled[i] = br.u(1))) { features.altLf[i] = static_cast<int16_t>(br.s(6)); }
                    if ((features.refEnabled[i] = br.u(1))) { features.refValue[i] = static_cast<uint8_t>(br.u(2)); }
                    features.skipEnabled[i] = br.u(1);
                }
            }
        }

        uint32_t sb64Cols = (h.width + 63u) / 64u, minLog2 = 0, maxLog2 = 1;
        while ((64u << minLog2) < sb64Cols) { minLog2++; }
        while ((sb64Cols >> maxLog2) >= 4) { maxLog2++; }
        maxLog2--;
        h.log2TileCols = static_cast<uint8_t>(minLog2);
        while (h.log2TileCols < maxLog2 && br.u(1)) { h.log2TileCols++; }
        h.log2TileRows = static_cast<uint8_t>(br.u(1));
        if (h.log2TileRows) { h.log2TileRows += br.u(1); }

        h.compressedHeaderSize = static_cast<uint16_t>(br.u(16));
        h.uncompressedHeaderSize = static_cast<uint32_t>(br.bytesUsed());
        if (br.overrun() || !h.width || !h.height || !h.compressedHeaderSize
            || h.compressedHeaderSize > size - h.uncompressedHeaderSize)
        {
            return Status::Malformed;
        }
        bitDepth = h.bitDepth;
        return Status::Ok;
    }

    //! Slot ids of the VCN reference list, as si_dec_vp9 assigns them from render_pic_list.
    void assignSlots(Surface target, const Surface refs[8], bool keyFrame, uint8_t &curId, uint8_t refIds[8]) {
        curId = NoSlot;
        for (int i = 0; i < 8; i++) { refIds[i] = NoSlot; }
        uint32_t validRef = UINT32_MAX;
        for (uint32_t i = 0; i < 9; i++) {
            if (renderPicList[i]) {
                bool found = false;
                for (uint32_t j = 0; j < 8; j++) {
                    if (!keyFrame && refs[j] && renderPicList[i] == refs[j]) {
                        refIds[j] = static_cast<uint8_t>(i);
                        validRef = j;
                        found = true;
                    }
                }
                if (!found) { renderPicList[i] = 0; }
            }
            if (curId == NoSlot && !renderPicList[i]) {
                renderPicList[i] = target;
                curId = static_cast<uint8_t>(i);
            }
        }
        for (uint32_t i = 0; i < 8; i++) {
            if (!keyFrame && refs[i] && refIds[i] == NoSlot) {
                refIds[i] = validRef == UINT32_MAX ? 0 : refIds[validRef];
            }
        }
    }

    public:
    //! Builds the decode message for one frame (build_vp9_msg). `probs` is the session's IT buffer: its segment
    //! part keeps the last values when segmentation is off, as in Mesa. `p010` selects 10-bit output without
    //! dithering. Returns `Status::Ok` with `message` filled, or the reason the frame cannot go to the VCN.
    Status build(const uint8_t *frame, size_t size, Surface target, const Surface refs[8], bool p010,
                 Message &message, ProbsSegment &probs, Header *headerOut = nullptr, uint8_t expectedBitDepth = 0) {
        Header h;
        Status status = parse(frame, size, h, expectedBitDepth);
        if (headerOut) { *headerOut = h; }
        if (status != Status::Ok) { return status; }

        const bool usePrevFrameMvs =
            prevShowFrame && !h.errorResilient && prevWidth == h.width && prevHeight == h.height;
        prevWidth = h.width, prevHeight = h.height, prevShowFrame = h.showFrame;
        for (int i = 0; i < 8; i++) {
            if (h.refreshFrameFlags & (1u << i)) { slotWidth[i] = h.width, slotHeight[i] = h.height; }
        }

        Message m {};
        m.frameHeaderFlags = (h.frameType ? HeaderFlag::FrameType : 0) |
                             (h.errorResilient ? HeaderFlag::ErrorResilient : 0) |
                             (h.intraOnly ? HeaderFlag::IntraOnly : 0) |
                             (h.allowHighPrecisionMv ? HeaderFlag::HighPrecisionMv : 0) |
                             (h.refreshFrameContext ? HeaderFlag::RefreshFrameContext : 0) |
                             (h.frameParallel ? HeaderFlag::FrameParallel : 0) |
                             (h.modeRefDeltaEnabled ? HeaderFlag::ModeRefDeltaEnabled : 0) |
                             (h.modeRefDeltaUpdate ? HeaderFlag::ModeRefDeltaUpdate : 0) |
                             (usePrevFrameMvs ? HeaderFlag::UsePrevFrameMvs : 0) | HeaderFlag::UseUncompressedHeader;
        if (h.segEnabled) {
            m.frameHeaderFlags |= HeaderFlag::SegmentationEnabled |
                                  (h.segUpdateMap ? HeaderFlag::SegmentationUpdateMap : 0) |
                                  (h.segTemporalUpdate ? HeaderFlag::SegmentationTemporalUpdate : 0) |
                                  (h.segUpdateData ? HeaderFlag::SegmentationUpdateData : 0);
        }
        uint8_t refIds[8];
        assignSlots(target, refs, h.frameType == 0, m.currPicIdx, refIds);
        m.frameContextIdx = h.frameContextIdx;
        m.resetFrameContext = h.resetFrameContext;
        m.interpFilter = h.interpFilter;
        m.filterLevel = h.filterLevel;
        m.sharpnessLevel = h.sharpness;
        m.baseQIndex = h.baseQIndex;
        m.yDcDeltaQ = h.yDcDeltaQ;
        m.uvAcDeltaQ = h.uvAcDeltaQ;
        m.uvDcDeltaQ = h.uvDcDeltaQ;
        m.log2TileCols = h.log2TileCols;
        m.log2TileRows = h.log2TileRows;
        m.chromaFormat = 1;
        m.bitDepthLumaMinus8 = m.bitDepthChromaMinus8 = static_cast<uint8_t>(h.bitDepth - 8);
        // Mesa keeps the header length in a byte (pipe_vp9_picture_desc::frame_header_length_in_bytes).
        m.uncompressedHeaderSize = static_cast<uint8_t>(h.uncompressedHeaderSize);
        m.compressedHeaderSize = h.compressedHeaderSize;
        if (h.bitDepth > 8) {
            if (p010) {
                m.p010Mode = m.msbMode = 1;
            } else {
                m.luma10to8 = m.chroma10to8 = DitheringRandom;
            }
        }

        // Segment features as si_dec_vp9 packs them: [0] alt quant, [1] alt lf, [2] reference, [3] unused.
        int16_t featureData[8][4] {};
        uint8_t featureMask[8] {};
        if (h.segEnabled) {
            for (int i = 0; i < 8; i++) {
                featureData[i][0] = features.altQ[i];
                featureData[i][1] = features.altLf[i];
                featureData[i][2] = features.refValue[i];
                featureMask[i]    = static_cast<uint8_t>(features.altQEnabled[i] | features.altLfEnabled[i] << 1
                                                         | features.refEnabled[i] << 2 | features.skipEnabled[i] << 3);
            }
        }
        const int32_t scale = 1 << (h.filterLevel >> 5);
        auto clampLevel = [](int32_t v) { return static_cast<uint8_t>(v < 0 ? 0 : v > 63 ? 63 : v); };
        for (int seg = 0; seg < 8; seg++) {
            int32_t base = h.filterLevel;
            if (featureMask[seg] & 2) {
                int32_t data = featureData[seg][1];
                if (absDelta != 1) { data += base; }
                base = clampLevel(data);
            }
            if (!h.modeRefDeltaEnabled) {
                m.lfAdjLevel[seg][0][0] = static_cast<uint8_t>(base);
            } else {
                m.lfAdjLevel[seg][0][0] = clampLevel(base + refDeltas[0] * scale);
            }
            for (int ref = 1; ref < 4; ref++) {
                for (int mode = 0; mode < 2; mode++) {
                    m.lfAdjLevel[seg][ref][mode] =
                        h.modeRefDeltaEnabled ? clampLevel(base + (refDeltas[ref] + modeDeltas[mode]) * scale) :
                                                static_cast<uint8_t>(base);
                }
            }
        }
        for (int i = 0; i < 8; i++) { m.refFrameMap[i] = refIds[i]; }
        for (int i = 0; i < 3; i++) {
            m.frameRefs[i] = refIds[h.refFrameIdx[i]];
            m.refFrameSignBias[i] = h.refFrameSignBias[i + 1];
        }
        if (h.segEnabled) {
            for (int i = 0; i < 8; i++) {
                probs.seg.featureData[i] = (static_cast<uint32_t>(featureData[i][0]) & 0xFFFF) |
                                           (static_cast<uint32_t>(featureData[i][1]) & 0xFF) << 16 |
                                           (static_cast<uint32_t>(featureData[i][2]) & 0xF) << 24;
                probs.seg.featureMask[i] = featureMask[i];
            }
            for (int i = 0; i < 7; i++) { probs.seg.treeProbs[i] = treeProbs[i]; }
            for (int i = 0; i < 3; i++) { probs.seg.predProbs[i] = predProbs[i]; }
            probs.seg.absDelta = absDelta;
        }
        message = m;
        return Status::Ok;
    }
};

}    // namespace VP9Message
