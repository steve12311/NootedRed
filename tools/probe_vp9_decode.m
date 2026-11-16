// VP9 VideoToolbox decode probe: reads an IVF file, builds a vpcC record, registers VP9 like browsers do and
// decodes every frame with the hardware decoder required. Reports session/frame status and an output digest.
// Build: clang -fobjc-arc -framework VideoToolbox -framework CoreMedia -framework CoreVideo -framework Foundation -framework CoreGraphics \
//        tools/probe_vp9_decode.m -o build/vp9/probe_vp9_decode
// Input: ffmpeg -i clip.webm -c copy -f ivf clip.ivf
// VP9_DUMP=path writes the visible luma and CbCr (NV12) of each output frame, for comparison with ffmpeg -pix_fmt nv12.
// VP9_PERF=1 decodes asynchronously without hashing the frames and prints the decode frame rate.
// VP9_INPROCESS=1 runs the decoder inside this process (VTApplyRestrictions), so it can be debugged with lldb.
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>
#include <stdatomic.h>

extern const CFStringRef kVTVideoDecoderSpecification_AllowVP9Decode __attribute__((weak_import));
// Exported by VideoToolbox; bit 0 asks for video decoders in this process.
extern OSStatus VTApplyRestrictions(uint32_t restrictions) __attribute__((weak_import));

static atomic_uint okFrames, badFrames;
static atomic_int  lastError;
static uint64_t    digest = 1469598103934665603ULL;
static FILE*       dump;    // VP9_DUMP=path: visible luma then CbCr of every output frame, in output order
static bool        perf;    // VP9_PERF=1: asynchronous decoding, no hashing or dump, prints the frame rate

static void decoded(void* ctx, void* frame, OSStatus status, VTDecodeInfoFlags flags, CVImageBufferRef image,
                    CMTime pts, CMTime duration)
{
    (void)ctx, (void)frame, (void)pts, (void)duration;
    if (status || !image || (flags & kVTDecodeInfo_FrameDropped)) {
        atomic_fetch_add(&badFrames, 1);
        atomic_store(&lastError, status ? status : -1);
        return;
    }
    if (getenv("VP9_REQUIRE_IOSURFACE") && !CVPixelBufferGetIOSurface(image)) {
        atomic_fetch_add(&badFrames, 1);
        atomic_store(&lastError, -2);
        return;
    }
    const unsigned index = atomic_fetch_add(&okFrames, 1);
    // Report the buffer and clean aperture of the first frame and whenever they change (resolution changes).
    static size_t lastW, lastH;
    static CGRect lastClean;
    const size_t  bufW = CVPixelBufferGetWidth(image), bufH = CVPixelBufferGetHeight(image);
    const CGRect  clean = CVImageBufferGetCleanRect(image);
    if (bufW != lastW || bufH != lastH || !CGRectEqualToRect(clean, lastClean)) {
        printf("frame %u: buffer %zux%zu, clean %gx%g at %g,%g, IOSurface=%u\n", index, bufW, bufH, clean.size.width,
               clean.size.height, clean.origin.x, clean.origin.y, CVPixelBufferGetIOSurface(image) != NULL);
        lastW = bufW, lastH = bufH, lastClean = clean;
    }
    if (perf || CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly)) { return; }
    // Hash only the visible luma plane.
    const uint8_t* base   = (const uint8_t*)CVPixelBufferGetBaseAddressOfPlane(image, 0);
    size_t         stride = CVPixelBufferGetBytesPerRowOfPlane(image, 0);
    size_t         w = CVPixelBufferGetWidthOfPlane(image, 0), h = CVPixelBufferGetHeightOfPlane(image, 0);
    for (size_t y = 0; base && y < h; y++)
        for (size_t x = 0; x < w; x++) digest = (digest ^ base[y * stride + x]) * 1099511628211ULL;
    for (size_t y = 0; dump && base && y < h; y++) fwrite(base + y * stride, 1, w, dump);
    // Then the interleaved CbCr plane (NV12), visible part only: the dump matches ffmpeg -pix_fmt nv12.
    if (dump && CVPixelBufferGetPlaneCount(image) > 1) {
        const uint8_t* uv = (const uint8_t*)CVPixelBufferGetBaseAddressOfPlane(image, 1);
        size_t uvStride = CVPixelBufferGetBytesPerRowOfPlane(image, 1);
        size_t uvW = CVPixelBufferGetWidthOfPlane(image, 1), uvH = CVPixelBufferGetHeightOfPlane(image, 1);
        for (size_t y = 0; uv && y < uvH; y++) fwrite(uv + y * uvStride, 1, uvW * 2, dump);
    }
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
}

static uint32_t readBits(const uint8_t* data, uint32_t* bit, uint32_t count)
{
    uint32_t value = 0;
    for (uint32_t i = 0; i < count; i++, (*bit)++) value = (value << 1) | ((data[*bit / 8] >> (7 - *bit % 8)) & 1);
    return value;
}

int main(int argc, char** argv)
{
    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr, "usage: %s clip.ivf [maxFrames]\n", argv[0]);
            return 2;
        }
        if (getenv("VP9_INPROCESS")) {
            printf("VTApplyRestrictions(in process) = %d\n", VTApplyRestrictions ? (int)VTApplyRestrictions(1) : -1);
        }
        if (getenv("VP9_DUMP")) dump = fopen(getenv("VP9_DUMP"), "wb");
        perf = getenv("VP9_PERF") && !dump;
        NSData* file = [NSData dataWithContentsOfFile:@(argv[1])];
        const uint8_t* d = (const uint8_t*)file.bytes;
        if (file.length < 32 || memcmp(d, "DKIF", 4) || memcmp(d + 8, "VP90", 4)) {
            fprintf(stderr, "not a VP9 IVF file\n");
            return 2;
        }
        const int32_t width = OSReadLittleInt16(d, 12), height = OSReadLittleInt16(d, 14);
        const uint32_t maxFrames = argc > 2 ? (uint32_t)atoi(argv[2]) : UINT32_MAX;

        // Profile and bit depth from the first frame's uncompressed header (a keyframe), as a browser's vpcC carries
        // them: frame_marker(2) profile_low(1) profile_high(1) [reserved(1) for profile 3] ... then, for a keyframe,
        // show_existing(1) frame_type(1) show_frame(1) error_resilient(1) sync_code(24) ten_or_twelve_bit(1, profile>=2).
        uint32_t profile = 0, bitDepth = 8;
        if (file.length > 32 + 12 + 5) {
            const uint8_t* f = d + 32 + 12;
            uint32_t bit = 0;
            readBits(f, &bit, 2);
            profile = readBits(f, &bit, 1);
            profile |= readBits(f, &bit, 1) << 1;
            if (profile == 3) readBits(f, &bit, 1);
            if (!readBits(f, &bit, 1) && !readBits(f, &bit, 1)) {    // not show_existing, key frame
                readBits(f, &bit, 2);
                readBits(f, &bit, 24);
                if (profile >= 2) bitDepth = readBits(f, &bit, 1) ? 12 : 10;
            }
        }
        printf("first frame: profile %u, %u bit\n", profile, bitDepth);
        // vpcC: version 1, level 3.0, 4:2:0 colocated, limited range, BT.709, no init data.
        const uint8_t vpcC[12] = {1, 0, 0, 0, (uint8_t)profile, 30, (uint8_t)((bitDepth << 4) | (1 << 1)), 1, 1, 1, 0, 0};
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
        uint64_t start = 0;
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
                if (!sent) start = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
                status = VTDecompressionSessionDecodeFrame(
                    session, sample, perf ? kVTDecodeFrame_EnableAsynchronousDecompression : 0, NULL, &info);
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
        const double seconds = (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - start) / 1e9;
        if (perf) printf("decode %.2f s, %.1f fps\n", seconds, atomic_load(&okFrames) / seconds);
        printf("frames sent %u, submit errors %u (first %d), decoded ok %u, bad %u (last %d), luma digest %016llx\n",
               sent, submitErrors, (int)firstSubmitError, atomic_load(&okFrames), atomic_load(&badFrames),
               atomic_load(&lastError), digest);
        if (dump) fclose(dump);
        VTDecompressionSessionInvalidate(session);
        CFRelease(session);
        CFRelease(format);
        return atomic_load(&okFrames) == sent && !submitErrors ? 0 : 1;
    }
}
