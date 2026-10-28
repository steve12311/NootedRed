// 由 tools/vpf/build_gl_evidence.py --fixtures --write 生成，不手工编辑。
// 只记录参考页/补丁块证据；运行时仍由 UserOpenGLResolver 动态核验。
#pragma once
#include <UserOpenGLData.hpp>

namespace VPF
{
namespace GLManifestData
{

struct PageEvidence
{
    UInt32 major, deviceID, kind, blockSize;
    UInt64 pageFileOffset, blockOffsetInPage;
    UInt32 functionIndex, instructionOffset;
    UInt64 pageDigest, originalDigest, patchedDigest;
    const UInt8* page;
    const UInt8* original;
    const UInt8* patched;
    const char* source;
    const char* build;
    const char* sourceSHA;
};

inline constexpr PageEvidence Evidence[] = {
    {23, 0x15D8, 0, 256,
     0xE000ULL, 0xB04ULL, 0, 0x128,
     0x0DE6DDC98D7A4A92ULL, 0xD33EE9D63D966EC7ULL, 0x66187852330762F7ULL,
     UserOpenGLData::Page23_0, UserOpenGLData::OS23PCI15D8Patches[0].original, UserOpenGLData::OS23PCI15D8Patches[0].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x15D8, 1, 142,
     0x8000ULL, 0xB28ULL, 1, 0x0,
     0x86DE707F0EEEA29FULL, 0x8069474DA410F373ULL, 0x4EDA087DF8888A7AULL,
     UserOpenGLData::Page23_1, UserOpenGLData::OS23PCI15D8Patches[1].original, UserOpenGLData::OS23PCI15D8Patches[1].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x15DD, 0, 256,
     0xE000ULL, 0xB04ULL, 0, 0x128,
     0x0DE6DDC98D7A4A92ULL, 0xD33EE9D63D966EC7ULL, 0x6FB45DAC3F415D04ULL,
     UserOpenGLData::Page23_0, UserOpenGLData::OS23PCI15DDPatches[0].original, UserOpenGLData::OS23PCI15DDPatches[0].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x15DD, 1, 142,
     0x8000ULL, 0xB28ULL, 1, 0x0,
     0x86DE707F0EEEA29FULL, 0x8069474DA410F373ULL, 0xD65433FE2C0CB99BULL,
     UserOpenGLData::Page23_1, UserOpenGLData::OS23PCI15DDPatches[1].original, UserOpenGLData::OS23PCI15DDPatches[1].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x15E7, 0, 256,
     0xE000ULL, 0xB04ULL, 0, 0x128,
     0x0DE6DDC98D7A4A92ULL, 0xD33EE9D63D966EC7ULL, 0xCABC14914717BFDEULL,
     UserOpenGLData::Page23_0, UserOpenGLData::OS23PCI15E7Patches[0].original, UserOpenGLData::OS23PCI15E7Patches[0].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x15E7, 1, 142,
     0x8000ULL, 0xB28ULL, 1, 0x0,
     0x86DE707F0EEEA29FULL, 0x8069474DA410F373ULL, 0x4B8F4754654FA60DULL,
     UserOpenGLData::Page23_1, UserOpenGLData::OS23PCI15E7Patches[1].original, UserOpenGLData::OS23PCI15E7Patches[1].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x1636, 0, 256,
     0xE000ULL, 0xB04ULL, 0, 0x128,
     0x0DE6DDC98D7A4A92ULL, 0xD33EE9D63D966EC7ULL, 0xDD08232ED2F93D1AULL,
     UserOpenGLData::Page23_0, UserOpenGLData::OS23PCI1636Patches[0].original, UserOpenGLData::OS23PCI1636Patches[0].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x1636, 1, 142,
     0x8000ULL, 0xB28ULL, 1, 0x0,
     0x86DE707F0EEEA29FULL, 0x8069474DA410F373ULL, 0xF4F03A00F000B5EDULL,
     UserOpenGLData::Page23_1, UserOpenGLData::OS23PCI1636Patches[1].original, UserOpenGLData::OS23PCI1636Patches[1].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x1638, 0, 256,
     0xE000ULL, 0xB04ULL, 0, 0x128,
     0x0DE6DDC98D7A4A92ULL, 0xD33EE9D63D966EC7ULL, 0xF0FD29D0C3A8B190ULL,
     UserOpenGLData::Page23_0, UserOpenGLData::OS23PCI1638Patches[0].original, UserOpenGLData::OS23PCI1638Patches[0].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x1638, 1, 142,
     0x8000ULL, 0xB28ULL, 1, 0x0,
     0x86DE707F0EEEA29FULL, 0x8069474DA410F373ULL, 0xF351500F13210CFBULL,
     UserOpenGLData::Page23_1, UserOpenGLData::OS23PCI1638Patches[1].original, UserOpenGLData::OS23PCI1638Patches[1].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x164C, 0, 256,
     0xE000ULL, 0xB04ULL, 0, 0x128,
     0x0DE6DDC98D7A4A92ULL, 0xD33EE9D63D966EC7ULL, 0xF778E3C50DAE9D04ULL,
     UserOpenGLData::Page23_0, UserOpenGLData::OS23PCI164CPatches[0].original, UserOpenGLData::OS23PCI164CPatches[0].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {23, 0x164C, 1, 142,
     0x8000ULL, 0xB28ULL, 1, 0x0,
     0x86DE707F0EEEA29FULL, 0x8069474DA410F373ULL, 0xCFF296F559A9C487ULL,
     UserOpenGLData::Page23_1, UserOpenGLData::OS23PCI164CPatches[1].original, UserOpenGLData::OS23PCI164CPatches[1].patched,
     "opengl-compat.json", "Sonoma", "f2efc174a9ef5270abe2b06ffa841ee0970ee9c8"},
    {24, 0x15D8, 0, 256,
     0xE000ULL, 0x68ULL, 0, 0x128,
     0xE18DB215D6994568ULL, 0xD33EE9D63D966EC7ULL, 0x66187852330762F7ULL,
     UserOpenGLData::Page24_0, UserOpenGLData::OS24PCI15D8Patches[0].original, UserOpenGLData::OS24PCI15D8Patches[0].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x15D8, 1, 142,
     0x8000ULL, 0x134ULL, 1, 0x0,
     0x8AA45D13F05AD3A4ULL, 0x8069474DA410F373ULL, 0x4EDA087DF8888A7AULL,
     UserOpenGLData::Page24_1, UserOpenGLData::OS24PCI15D8Patches[1].original, UserOpenGLData::OS24PCI15D8Patches[1].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x15DD, 0, 256,
     0xE000ULL, 0x68ULL, 0, 0x128,
     0xE18DB215D6994568ULL, 0xD33EE9D63D966EC7ULL, 0x6FB45DAC3F415D04ULL,
     UserOpenGLData::Page24_0, UserOpenGLData::OS24PCI15DDPatches[0].original, UserOpenGLData::OS24PCI15DDPatches[0].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x15DD, 1, 142,
     0x8000ULL, 0x134ULL, 1, 0x0,
     0x8AA45D13F05AD3A4ULL, 0x8069474DA410F373ULL, 0xD65433FE2C0CB99BULL,
     UserOpenGLData::Page24_1, UserOpenGLData::OS24PCI15DDPatches[1].original, UserOpenGLData::OS24PCI15DDPatches[1].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x15E7, 0, 256,
     0xE000ULL, 0x68ULL, 0, 0x128,
     0xE18DB215D6994568ULL, 0xD33EE9D63D966EC7ULL, 0xCABC14914717BFDEULL,
     UserOpenGLData::Page24_0, UserOpenGLData::OS24PCI15E7Patches[0].original, UserOpenGLData::OS24PCI15E7Patches[0].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x15E7, 1, 142,
     0x8000ULL, 0x134ULL, 1, 0x0,
     0x8AA45D13F05AD3A4ULL, 0x8069474DA410F373ULL, 0x4B8F4754654FA60DULL,
     UserOpenGLData::Page24_1, UserOpenGLData::OS24PCI15E7Patches[1].original, UserOpenGLData::OS24PCI15E7Patches[1].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x1636, 0, 256,
     0xE000ULL, 0x68ULL, 0, 0x128,
     0xE18DB215D6994568ULL, 0xD33EE9D63D966EC7ULL, 0xDD08232ED2F93D1AULL,
     UserOpenGLData::Page24_0, UserOpenGLData::OS24PCI1636Patches[0].original, UserOpenGLData::OS24PCI1636Patches[0].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x1636, 1, 142,
     0x8000ULL, 0x134ULL, 1, 0x0,
     0x8AA45D13F05AD3A4ULL, 0x8069474DA410F373ULL, 0xF4F03A00F000B5EDULL,
     UserOpenGLData::Page24_1, UserOpenGLData::OS24PCI1636Patches[1].original, UserOpenGLData::OS24PCI1636Patches[1].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x1638, 0, 256,
     0xE000ULL, 0x68ULL, 0, 0x128,
     0xE18DB215D6994568ULL, 0xD33EE9D63D966EC7ULL, 0xF0FD29D0C3A8B190ULL,
     UserOpenGLData::Page24_0, UserOpenGLData::OS24PCI1638Patches[0].original, UserOpenGLData::OS24PCI1638Patches[0].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x1638, 1, 142,
     0x8000ULL, 0x134ULL, 1, 0x0,
     0x8AA45D13F05AD3A4ULL, 0x8069474DA410F373ULL, 0xF351500F13210CFBULL,
     UserOpenGLData::Page24_1, UserOpenGLData::OS24PCI1638Patches[1].original, UserOpenGLData::OS24PCI1638Patches[1].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x164C, 0, 256,
     0xE000ULL, 0x68ULL, 0, 0x128,
     0xE18DB215D6994568ULL, 0xD33EE9D63D966EC7ULL, 0xF778E3C50DAE9D04ULL,
     UserOpenGLData::Page24_0, UserOpenGLData::OS24PCI164CPatches[0].original, UserOpenGLData::OS24PCI164CPatches[0].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {24, 0x164C, 1, 142,
     0x8000ULL, 0x134ULL, 1, 0x0,
     0x8AA45D13F05AD3A4ULL, 0x8069474DA410F373ULL, 0xCFF296F559A9C487ULL,
     UserOpenGLData::Page24_1, UserOpenGLData::OS24PCI164CPatches[1].original, UserOpenGLData::OS24PCI164CPatches[1].patched,
     "opengl-compat.json", "Sequoia", "6fff1c4afd698404d3dfd79988810e8e07c5f2ec"},
    {25, 0x15D8, 0, 256,
     0xE000ULL, 0xA8ULL, 0, 0x128,
     0xCE46718D4208D1A0ULL, 0xD33EE9D63D966EC7ULL, 0x66187852330762F7ULL,
     UserOpenGLData::Page25_0, UserOpenGLData::OS25PCI15D8Patches[0].original, UserOpenGLData::OS25PCI15D8Patches[0].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x15D8, 1, 142,
     0x8000ULL, 0x19EULL, 1, 0x0,
     0x983E9D3070084BD8ULL, 0x8069474DA410F373ULL, 0x4EDA087DF8888A7AULL,
     UserOpenGLData::Page25_1, UserOpenGLData::OS25PCI15D8Patches[1].original, UserOpenGLData::OS25PCI15D8Patches[1].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x15DD, 0, 256,
     0xE000ULL, 0xA8ULL, 0, 0x128,
     0xCE46718D4208D1A0ULL, 0xD33EE9D63D966EC7ULL, 0x6FB45DAC3F415D04ULL,
     UserOpenGLData::Page25_0, UserOpenGLData::OS25PCI15DDPatches[0].original, UserOpenGLData::OS25PCI15DDPatches[0].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x15DD, 1, 142,
     0x8000ULL, 0x19EULL, 1, 0x0,
     0x983E9D3070084BD8ULL, 0x8069474DA410F373ULL, 0xD65433FE2C0CB99BULL,
     UserOpenGLData::Page25_1, UserOpenGLData::OS25PCI15DDPatches[1].original, UserOpenGLData::OS25PCI15DDPatches[1].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x15E7, 0, 256,
     0xE000ULL, 0xA8ULL, 0, 0x128,
     0xCE46718D4208D1A0ULL, 0xD33EE9D63D966EC7ULL, 0xCABC14914717BFDEULL,
     UserOpenGLData::Page25_0, UserOpenGLData::OS25PCI15E7Patches[0].original, UserOpenGLData::OS25PCI15E7Patches[0].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x15E7, 1, 142,
     0x8000ULL, 0x19EULL, 1, 0x0,
     0x983E9D3070084BD8ULL, 0x8069474DA410F373ULL, 0x4B8F4754654FA60DULL,
     UserOpenGLData::Page25_1, UserOpenGLData::OS25PCI15E7Patches[1].original, UserOpenGLData::OS25PCI15E7Patches[1].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x1636, 0, 256,
     0xE000ULL, 0xA8ULL, 0, 0x128,
     0xCE46718D4208D1A0ULL, 0xD33EE9D63D966EC7ULL, 0xDD08232ED2F93D1AULL,
     UserOpenGLData::Page25_0, UserOpenGLData::OS25PCI1636Patches[0].original, UserOpenGLData::OS25PCI1636Patches[0].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x1636, 1, 142,
     0x8000ULL, 0x19EULL, 1, 0x0,
     0x983E9D3070084BD8ULL, 0x8069474DA410F373ULL, 0xF4F03A00F000B5EDULL,
     UserOpenGLData::Page25_1, UserOpenGLData::OS25PCI1636Patches[1].original, UserOpenGLData::OS25PCI1636Patches[1].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x1638, 0, 256,
     0xE000ULL, 0xA8ULL, 0, 0x128,
     0xCE46718D4208D1A0ULL, 0xD33EE9D63D966EC7ULL, 0xF0FD29D0C3A8B190ULL,
     UserOpenGLData::Page25_0, UserOpenGLData::OS25PCI1638Patches[0].original, UserOpenGLData::OS25PCI1638Patches[0].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x1638, 1, 142,
     0x8000ULL, 0x19EULL, 1, 0x0,
     0x983E9D3070084BD8ULL, 0x8069474DA410F373ULL, 0xF351500F13210CFBULL,
     UserOpenGLData::Page25_1, UserOpenGLData::OS25PCI1638Patches[1].original, UserOpenGLData::OS25PCI1638Patches[1].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x164C, 0, 256,
     0xE000ULL, 0xA8ULL, 0, 0x128,
     0xCE46718D4208D1A0ULL, 0xD33EE9D63D966EC7ULL, 0xF778E3C50DAE9D04ULL,
     UserOpenGLData::Page25_0, UserOpenGLData::OS25PCI164CPatches[0].original, UserOpenGLData::OS25PCI164CPatches[0].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
    {25, 0x164C, 1, 142,
     0x8000ULL, 0x19EULL, 1, 0x0,
     0x983E9D3070084BD8ULL, 0x8069474DA410F373ULL, 0xCFF296F559A9C487ULL,
     UserOpenGLData::Page25_1, UserOpenGLData::OS25PCI164CPatches[1].original, UserOpenGLData::OS25PCI164CPatches[1].patched,
     "opengl-compat.json", "Tahoe", "fcd82095bbe66b916177bdefa6a711c9716666af"},
};
inline constexpr UInt32 EvidenceCount = sizeof(Evidence) / sizeof(Evidence[0]);
static_assert(EvidenceCount == 36);

constexpr const PageEvidence* find(const UInt32 major, const UInt32 deviceID, const UInt32 kind)
{
    for (UInt32 i = 0; i < EvidenceCount; ++i) {
        const auto& entry = Evidence[i];
        if (entry.major == major && entry.deviceID == deviceID && entry.kind == kind) { return &entry; }
    }
    return nullptr;
}

// 只对补丁块计算；rel32 字段按 0 处理，避免文件地址变化影响证据摘要。
inline UInt64 digest(const UInt8* bytes, const UInt32 size, const UserCacheResolver::Reference* refs,
                     const UInt32 count, const UInt32 start)
{
    UInt64 value = 14695981039346656037ULL;
    for (UInt32 i = 0; i < size; ++i) {
        UInt8 byte = bytes[i];
        if (UserCacheResolver::skipped(start + i, refs, count)) { byte = 0; }
        value ^= byte;
        value *= 1099511628211ULL;
    }
    return value;
}

}    // namespace GLManifestData
}    // namespace VPF
