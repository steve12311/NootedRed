// 只修改本测试进程的 COW 页；默认只查询能力，不解码 AV1。
#import <VideoToolbox/VideoToolbox.h>
#import <AVFoundation/AVFoundation.h>
extern "C" OSStatus VTApplyRestrictions(uint32_t) __attribute__((weak_import));
#define main vp9ProbeMain
#include "../probe_vp9_decode.m"
#undef main
#include <dlfcn.h>
#include <sys/mman.h>
#include <array>
#include <cassert>
#include <vector>
#include "vt-admission-reference.hpp"

using Select = OSStatus (*)(OSType, CFAllocatorRef, int, CFDictionaryRef, CFIndex, void**, void**, Boolean*, CFIndex*);
static constexpr std::array<OSType, 5> Codecs = {kCMVideoCodecType_AV1, kCMVideoCodecType_VP9,
    kCMVideoCodecType_H264, kCMVideoCodecType_HEVC, 'qvp9'};
using Results = std::array<std::array<OSStatus, 3>, Codecs.size()>;

static Results selections(Select select)
{
    NSDictionary* spec = @{(__bridge NSString*)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder: @YES};
    Results out{};
    for (size_t c = 0; c < Codecs.size(); ++c) {
        for (int mode = 0; mode < 3; ++mode) {
            out[c][mode] = select(Codecs[c], nullptr, mode, (__bridge CFDictionaryRef)spec, 0,
                                  nullptr, nullptr, nullptr, nullptr);
        }
    }
    return out;
}

static void install(uintptr_t address, const uint8_t* replacement)
{
    const auto page = address & ~uintptr_t{4095};
    const auto length = ((address + sizeof(Native) + 4095) & ~uintptr_t{4095}) - page;
    std::vector<uint8_t> saved(length);
    memcpy(saved.data(), reinterpret_cast<void*>(page), length);
    void* copy = mmap(reinterpret_cast<void*>(page), length, PROT_READ | PROT_WRITE,
                       MAP_FIXED | MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(copy != MAP_FAILED);
    memcpy(copy, saved.data(), length);
    memcpy(reinterpret_cast<void*>(address), replacement, sizeof(Native));
    assert(mprotect(copy, length, PROT_READ | PROT_EXEC) == 0);
}

int main(int argc, char** argv)
{
    @autoreleasepool {
        const auto slide = reinterpret_cast<uintptr_t>(VTIsHardwareDecodeSupported) - QueryAddress;
        const auto address = FilterAddress + slide;
        const bool native = memcmp(reinterpret_cast<void*>(address), Native, sizeof(Native)) == 0;
        const bool patched = memcmp(reinterpret_cast<void*>(address), Patched, sizeof(Patched)) == 0;
        if (!native && !patched) {
            fputs("Current VideoToolbox callback does not match the verified reference\n", stderr);
            return 2;
        }
        printf("VideoToolbox callback on entry: %s\n", patched ? "patched" : "native");
        if (patched) { install(address, Native); }
        auto entry = reinterpret_cast<const uint8_t*>(dlsym(RTLD_DEFAULT, "VTSelectAndCreateVideoDecoderInstance"));
        assert(entry && entry[0x20] == 0xE8);
        int32_t relative;
        memcpy(&relative, entry + 0x21, 4);
        auto select = reinterpret_cast<Select>(const_cast<uint8_t*>(entry + 0x25 + relative));
        VTRegisterSupplementalVideoDecoderIfAvailable(kCMVideoCodecType_VP9);
        (void)VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9);
        const auto before = selections(select);
        printf("native warm: AV1=%u VP9=%u H264=%u HEVC=%u\n",
               VTIsHardwareDecodeSupported(Codecs[0]), VTIsHardwareDecodeSupported(Codecs[1]),
               VTIsHardwareDecodeSupported(Codecs[2]), VTIsHardwareDecodeSupported(Codecs[3]));
        install(address, Patched);
        const auto after = selections(select);
        for (size_t c = 0; c < Codecs.size(); ++c) {
            for (int mode = 0; mode < 3; ++mode) {
                printf("codec=%08x mode=%d status=%d -> %d\n", Codecs[c], mode, before[c][mode], after[c][mode]);
                if (c >= 2 || mode == 2) { assert(before[c][mode] == after[c][mode]); }
            }
        }
        assert(!VTIsHardwareDecodeSupported(kCMVideoCodecType_AV1));
        assert(VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9));
        assert(VTIsHardwareDecodeSupported(kCMVideoCodecType_H264));
        assert(VTIsHardwareDecodeSupported(kCMVideoCodecType_HEVC));
        puts("PASS: generic VideoToolbox capability/default selection; native mode 2 and other codecs preserved");
        CMVideoFormatDescriptionRef format{nullptr};
        assert(argc == 3);
        AVURLAsset* asset = [AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:@(argv[2])] options:nil];
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        AVAssetTrack* track = [asset tracksWithMediaType:AVMediaTypeVideo].firstObject;
        format = (__bridge CMVideoFormatDescriptionRef)track.formatDescriptions.firstObject;
#pragma clang diagnostic pop
        assert(format && CMFormatDescriptionGetMediaSubType(format) == kCMVideoCodecType_AV1);
        NSDictionary* required = @{(__bridge NSString*)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder: @YES};
        VTDecompressionSessionRef session{nullptr};
        const auto status = VTDecompressionSessionCreate(nullptr, format, (__bridge CFDictionaryRef)required,
                                                         nullptr, nullptr, &session);
        printf("AV1 required-hardware create=%d session=%p\n", status, session);
        const bool noSession = !session;
        if (session) { VTDecompressionSessionInvalidate(session); CFRelease(session); }
        assert(status == kVTCouldNotFindVideoDecoderErr && noSession);
        setenv("VP9_REQUIRE_IOSURFACE", "1", 1);
        return vp9ProbeMain(2, argv);
    }
}
