#include <VP9Message.hpp>
#include <cassert>
#include <cstdio>
#include <vector>
using namespace VP9Message;

struct Bits
{
    std::vector<uint8_t> data;
    size_t               count = 0;
    void                 u(unsigned value, unsigned size)
    {
        for (unsigned i = size; i--;) {
            if (count % 8 == 0) { data.push_back(0); }
            data.back() |= ((value >> i) & 1) << (7 - count % 8);
            ++count;
        }
    }
};

enum class Kind
{
    Key,
    Inter,
    IntraOnly,
    ErrorResilient
};

static std::vector<uint8_t> frame(Kind kind, bool update, unsigned compressedSize = 1)
{
    Bits       b;
    const bool key = kind == Kind::Key, intra = kind == Kind::IntraOnly;
    const bool resilient = kind == Kind::ErrorResilient;
    b.u(2, 2);
    b.u(0, 2);
    b.u(0, 1);
    b.u(!key, 1);
    b.u(!intra, 1);
    b.u(resilient, 1);
    if (!key) {
        if (intra) { b.u(1, 1); }
        if (!resilient) { b.u(0, 2); }
    }
    if (key || intra) {
        b.u(0x498342, 24);
        if (key) {
            b.u(1, 3);
            b.u(0, 1);
        }
        if (intra) { b.u(0xFF, 8); }
        b.u(63, 16);
        b.u(63, 16);
        b.u(0, 1);
    }
    else {
        b.u(0, 8);
        for (unsigned i = 0; i < 3; ++i) {
            b.u(0, 3);
            b.u(0, 1);
        }
        b.u(1, 1);
        b.u(0, 1);
        b.u(0, 1);
        b.u(1, 1);
    }
    if (!resilient) {
        b.u(0, 1);
        b.u(1, 1);
    }
    b.u(0, 2);
    b.u(0, 6);
    b.u(0, 3);
    b.u(0, 1);
    b.u(20, 8);
    b.u(0, 3);
    b.u(1, 1);
    b.u(0, 1);
    b.u(update, 1);
    if (update) {
        b.u(1, 1);
        for (unsigned i = 0; i < 8; ++i) {
            b.u(i == 0, 1);
            if (i == 0) {
                b.u(10, 8);
                b.u(0, 1);
            }
            b.u(i == 0, 1);
            if (i == 0) {
                b.u(2, 6);
                b.u(0, 1);
            }
            b.u(i == 0, 1);
            if (i == 0) { b.u(3, 2); }
            b.u(i == 0, 1);
        }
    }
    b.u(0, 1);
    b.u(compressedSize, 16);
    b.data.resize(b.data.size() + 64);
    return b.data;
}

int main()
{
    Surface      refs[8]{};
    Message      m{};
    ProbsSegment p{};
    Header       h{};
    const auto   seed = frame(Kind::Key, true);
    for (Kind kind : {Kind::Key, Kind::IntraOnly, Kind::ErrorResilient, Kind::Inter}) {
        Session session;
        assert(session.build(seed.data(), seed.size(), 1, refs, false, m, p) == Status::Ok);
        assert(p.seg.featureMask[0] == 15 && p.seg.absDelta == 1);
        const auto next = frame(kind, false);
        p               = {};
        assert(session.build(next.data(), next.size(), 2, refs, false, m, p, &h) == Status::Ok);
        if (kind == Kind::Inter) {
            assert(p.seg.featureMask[0] == 15 && p.seg.featureData[0] == (3U << 24 | 2U << 16 | 10U));
            assert(p.seg.absDelta == 1);
        }
        else {
            for (unsigned i = 0; i < 8; ++i) { assert(p.seg.featureMask[i] == 0 && p.seg.featureData[i] == 0); }
            assert(p.seg.absDelta == 0);
        }
    }
    Session truncated;
    auto    shortFrame = frame(Kind::Key, false);
    assert(truncated.build(shortFrame.data(), shortFrame.size(), 3, refs, false, m, p, &h) == Status::Ok);
    shortFrame.resize(h.uncompressedHeaderSize);
    assert(truncated.build(shortFrame.data(), shortFrame.size(), 3, refs, false, m, p) == Status::Malformed);
    const auto emptyHeader = frame(Kind::Key, false, 0);
    assert(truncated.build(emptyHeader.data(), emptyHeader.size(), 3, refs, false, m, p) == Status::Malformed);
    puts("PASS VP9 parser: key/intra-only/error-resilient resets, inter persistence, compressed header bounds");
}
