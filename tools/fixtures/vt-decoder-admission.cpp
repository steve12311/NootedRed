#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

extern "C" uint32_t nativeConversion(void*, uint32_t);
extern "C" uint32_t admittedConversion(void*, uint32_t);

int main()
{
    uint32_t       random   = 0x938124;
    const uint32_t codecs[] = {0x39307076, 0x31307661, 0x39707671, 0x31637661, 0x31637668, 0x696D7264, 0x7F010020};
    for (unsigned i = 0; i < 12000; ++i) {
        random          = random * 1664525 + 1013904223;
        const auto name = i < std::size(codecs) ? codecs[i] : random & 0x7F7F7F7F;
        for (int32_t mode : {0, 1, 2, 3, 0x100, -1}) {
            std::array<uint8_t, 32> native{}, changed{};
            memcpy(native.data() + 0x14, &mode, sizeof(mode));
            changed           = native;
            const auto before = nativeConversion(native.data(), name);
            const auto after  = admittedConversion(changed.data(), name);
            assert(before == __builtin_bswap32(name) && after == before);
            int32_t expected = mode;
            if ((mode == 0 || mode == 1) && (name == 0x39307076 || name == 0x31307661)) { expected = 2; }
            std::array<uint8_t, 32> wanted = native;
            memcpy(wanted.data() + 0x14, &expected, sizeof(expected));
            assert(changed == wanted);
        }
    }
    puts("PASS: 72000 native/patched instruction comparisons; ASCII FourCC, query/create/other modes, other codecs");
}
