// VP9 VideoToolbox decode probe: reads an IVF file, builds a vpcC record, registers VP9 like browsers do and
// decodes every frame with the hardware decoder required. Reports session/frame status and an output digest.
// Build: clang -fobjc-arc -framework VideoToolbox -framework CoreMedia -framework CoreVideo -framework Foundation \
//        tools/probe_vp9_decode.m -o build/vp9/probe_vp9_decode
// Input: ffmpeg -i clip.webm -c copy -f ivf clip.ivf
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>
#include <stdatomic.h>

extern const CFStringRef kVTVideoDecoderSpecification_AllowVP9Decode __attribute__((weak_import));

static atomic_uint okFrames, badFrames;
static atomic_int  lastError;
static uint64_t    digest = 1469598103934665603ULL;

static void decoded(void* ctx, void* frame, OSStatus status, VTDecodeInfoFlags flags, CVImageBufferRef image,
                    CMTime pts, CMTime duration)
{
    (void)ctx, (void)frame, (void)pts, (void)duration;
    if (status || !image || (flags & kVTDecodeInfo_FrameDropped)) {
        atomic_fetch_add(&badFrames, 1);
        atomic_store(&lastError, status ? status : -1);
        return;
    }
    atomic_fetch_add(&okFrames, 1);
    if (CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly)) { return; }
    // Hash only the visible luma plane.
    const uint8_t* base   = CVPixelBufferGetBaseAddressOfPlane(image, 0);
    size_t         stride = CVPixelBufferGetBytesPerRowOfPlane(image, 0);
    size_t         w = CVPixelBufferGetWidthOfPlane(image, 0), h = CVPixelBufferGetHeightOfPlane(image, 0);
    for (size_t y = 0; base && y < h; y++)
        for (size_t x = 0; x < w; x++) digest = (digest ^ base[y * stride + x]) * 1099511628211ULL;
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
}

int main(int argc, char** argv)
{
    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr, "usage: %s clip.ivf [maxFrames]\n", argv[0]);
            return 2;
        }
        NSData* file = [NSData dataWithContentsOfFile:@(argv[1])];
        const uint8_t* d = file.bytes;
        if (file.length < 32 || memcmp(d, "DKIF", 4) || memcmp(d + 8, "VP90", 4)) {
            fprintf(stderr, "not a VP9 IVF file\n");
            return 2;
        }
        const int32_t width = OSReadLittleInt16(d, 12), height = OSReadLittleInt16(d, 14);
        const uint32_t maxFrames = argc > 2 ? (uint32_t)atoi(argv[2]) : UINT32_MAX;

        // vpcC: version 1, profile 0, level 3.0, 8 bit, 4:2:0 colocated, limited range, BT.709, no init data.
        const uint8_t vpcC[12] = {1, 0, 0, 0, 0, 30, (8 << 4) | (1 << 1), 1, 1, 1, 0, 0};
        NSDictionary* extensions = @{
            (__bridge NSString*)kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms :
                @{@"vpcC" : [NSData dataWithBytes:vpcC length:sizeof(vpcC)]}
        };
        CMVideoFormatDescriptionRef format = NULL;
        OSStatus status = CMVideoFormatDescriptionCreate(NULL, kCMVideoCodecType_VP9, width, height,
                                                         (__bridge CFDictionaryRef)extensions, &format);
        if (status) {
            printf("format description error %d\n", (int)status);
            return 1;
        }
        VTRegisterSupplementalVideoDecoderIfAvailable(kCMVideoCodecType_VP9);
        printf("%dx%d, VTIsHardwareDecodeSupported(vp09) = %d\n", width, height,
               VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9));
        NSMutableDictionary* spec = [@{
            (__bridge NSString*)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder : @YES
        } mutableCopy];
        if (kVTVideoDecoderSpecification_AllowVP9Decode)
            spec[(__bridge NSString*)kVTVideoDecoderSpecification_AllowVP9Decode] = @YES;
        NSDictionary* pixels = @{
            (__bridge NSString*)kCVPixelBufferPixelFormatTypeKey : @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)
        };
        VTDecompressionOutputCallbackRecord callback = {decoded, NULL};
        VTDecompressionSessionRef session = NULL;
        status = VTDecompressionSessionCreate(NULL, format, (__bridge CFDictionaryRef)spec,
                                              (__bridge CFDictionaryRef)pixels, &callback, &session);
        printf("VTDecompressionSessionCreate = %d\n", (int)status);
        if (status) { return 1; }

        size_t   offset = OSReadLittleInt16(d, 6);
        uint32_t sent = 0, submitErrors = 0;
        OSStatus firstSubmitError = 0;
        while (offset + 12 <= file.length && sent < maxFrames) {
            const uint32_t size = OSReadLittleInt32(d, offset);
            offset += 12;
            if (offset + size > file.length) { break; }
            CMBlockBufferRef block = NULL;
            CMSampleBufferRef sample = NULL;
            status = CMBlockBufferCreateWithMemoryBlock(NULL, (void*)(d + offset), size, kCFAllocatorNull, NULL, 0,
                                                        size, 0, &block);
            if (!status) {
                const size_t sizes[1] = {size};
                status = CMSampleBufferCreateReady(NULL, block, format, 1, 0, NULL, 1, sizes, &sample);
            }
            if (!status) {
                VTDecodeInfoFlags info = 0;
                status = VTDecompressionSessionDecodeFrame(session, sample, 0, NULL, &info);
            }
            if (status) {
                if (!submitErrors++) firstSubmitError = status;
            }
            if (sample) CFRelease(sample);
            if (block) CFRelease(block);
            offset += size;
            sent++;
        }
        VTDecompressionSessionWaitForAsynchronousFrames(session);
        printf("frames sent %u, submit errors %u (first %d), decoded ok %u, bad %u (last %d), luma digest %016llx\n",
               sent, submitErrors, (int)firstSubmitError, atomic_load(&okFrames), atomic_load(&badFrames),
               atomic_load(&lastError), digest);
        VTDecompressionSessionInvalidate(session);
        CFRelease(session);
        CFRelease(format);
        return atomic_load(&okFrames) == sent && !submitErrors ? 0 : 1;
    }
}
