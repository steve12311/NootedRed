// VideoToolbox 硬解回归：禁止软件回退，输出解码阶段、错误码及帧摘要。
#import <AVFoundation/AVFoundation.h>
#import <VideoToolbox/VideoToolbox.h>
#import <VideoDecodeAcceleration/VDADecoder.h>
#include <stdatomic.h>

typedef struct {
    atomic_uint frames;
    atomic_int error;
    uint64_t digest;
    size_t width, height;
} DecodeState;

// 10 位模式请求并校验 x420；摘要覆盖 16 位样本字节，排除行对齐填充。
static BOOL tenBit;

static void decoded(void* context, void* frame, OSStatus status, VTDecodeInfoFlags flags,
                    CVImageBufferRef image, CMTime timestamp, CMTime duration)
{
    DecodeState* state = context;
    if (status || !image || (flags & kVTDecodeInfo_FrameDropped)) {
        atomic_store(&state->error, status ? status : -1);
        return;
    }
    status = CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
    if (status) {
        atomic_store(&state->error, status);
        return;
    }
    // 固定请求 NV12 或 x420，只摘要有效像素，排除行对齐填充。
    const OSType expectedFormat = tenBit ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
                                         : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
    if (CVPixelBufferGetWidth(image) != state->width || CVPixelBufferGetHeight(image) != state->height
        || CVPixelBufferGetPixelFormatType(image) != expectedFormat
        || CVPixelBufferGetPlaneCount(image) != 2) {
        atomic_store(&state->error, -2);
    }
    else {
        uint64_t hash = state->digest;
        for (size_t plane = 0; plane < 2; plane++) {
            const uint8_t* base = CVPixelBufferGetBaseAddressOfPlane(image, plane);
            size_t stride = CVPixelBufferGetBytesPerRowOfPlane(image, plane);
            size_t width = CVPixelBufferGetWidthOfPlane(image, plane) * (plane ? 2 : 1) * (tenBit ? 2 : 1);
            for (size_t y = 0; y < CVPixelBufferGetHeightOfPlane(image, plane); y++) {
                for (size_t x = 0; x < width; x++) { hash = (hash ^ base[y * stride + x]) * 1099511628211ULL; }
            }
        }
        state->digest = hash;
        atomic_fetch_add(&state->frames, 1);
    }
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
}

static int report(NSMutableDictionary* result, NSString* stage, OSStatus status)
{
    result[@"stage"] = stage;
    result[@"status"] = @(status);
    NSData* data = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];
    if (!data) { return 2; }
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
    return status ? 1 : 0;
}

static void vdaDecoded(void* context, CFDictionaryRef frame, OSStatus status, uint32_t flags, CVImageBufferRef image)
{
    decoded(context, NULL, status, flags, image, kCMTimeInvalid, kCMTimeInvalid);
}

int main(int argc, const char* argv[])
{
    @autoreleasepool {
        NSMutableDictionary* result = [@{
            @"h264HardwareAdvertised": @(VTIsHardwareDecodeSupported(kCMVideoCodecType_H264)),
            @"hevcHardwareAdvertised": @(VTIsHardwareDecodeSupported(kCMVideoCodecType_HEVC)),
            @"processID": @(getpid()),
        } mutableCopy];
        if (argc == 2 && !strcmp(argv[1], "--capabilities")) { return report(result, @"capabilities", 0); }
        if ((argc != 3 && argc != 4) || (strcmp(argv[1], "--hardware") && strcmp(argv[1], "--software")
                         && strcmp(argv[1], "--vda")) || (argc == 4 && strcmp(argv[3], "--10bit"))) {
            fprintf(stderr, "Usage: %s --capabilities | (--hardware|--software|--vda) local-video.mp4 [--10bit]\n",
                    argv[0]);
            return 2;
        }
        tenBit = argc == 4;
        BOOL legacyVDA = !strcmp(argv[1], "--vda");
        BOOL hardware = !strcmp(argv[1], "--hardware") || legacyVDA;
        result[@"requireHardware"] = @(hardware);
        NSString* path = [NSString stringWithUTF8String:argv[2]];
        if (![[NSFileManager defaultManager] fileExistsAtPath:path]) { return report(result, @"input", -1); }
        AVURLAsset* asset = [AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:path] options:nil];
        // 本地短样本使用同步读取；进程由外层测试以 60 秒超时约束。
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        NSArray<AVAssetTrack*>* tracks = [asset tracksWithMediaType:AVMediaTypeVideo];
#pragma clang diagnostic pop
        if (tracks.count != 1) { return report(result, @"track", -1); }
        NSError* error = nil;
        AVAssetReader* reader = [[AVAssetReader alloc] initWithAsset:asset error:&error];
        if (!reader) { return report(result, @"reader", (OSStatus)error.code); }
        AVAssetReaderTrackOutput* output = [[AVAssetReaderTrackOutput alloc] initWithTrack:tracks[0]
                                                                          outputSettings:nil];
        if (![reader canAddOutput:output]) { return report(result, @"reader-output", -1); }
        [reader addOutput:output];
        if (![reader startReading]) { return report(result, @"reader-start", (OSStatus)reader.error.code); }
        CMSampleBufferRef sample = [output copyNextSampleBuffer];
        // AVAssetReader 可在数据前返回无格式、零样本的时间线标记。
        unsigned markers = 0;
        while (sample && !CMSampleBufferGetFormatDescription(sample)
               && CMSampleBufferGetNumSamples(sample) == 0 && markers < 8) {
            CFRelease(sample);
            sample = [output copyNextSampleBuffer];
            markers++;
        }
        result[@"initialMarkers"] = @(markers);
        if (!sample) { return report(result, @"first-sample", -1); }
        CMVideoFormatDescriptionRef format = CMSampleBufferGetFormatDescription(sample);
        if (!format || CMFormatDescriptionGetMediaType(format) != kCMMediaType_Video) {
            result[@"sampleCount"] = @(CMSampleBufferGetNumSamples(sample));
            result[@"sampleReady"] = @(CMSampleBufferDataIsReady(sample));
            result[@"sampleSize"] = @(CMSampleBufferGetTotalSampleSize(sample));
            result[@"formatPresent"] = @(format != NULL);
            result[@"mediaType"] = @(format ? CMFormatDescriptionGetMediaType(format) : 0);
            CFRelease(sample);
            [reader cancelReading];
            return report(result, @"sample-format", -1);
        }
        CMVideoDimensions dimensions = CMVideoFormatDescriptionGetDimensions(format);
        result[@"width"] = @(dimensions.width);
        result[@"height"] = @(dimensions.height);
        result[@"codec"] = @(CMFormatDescriptionGetMediaSubType(format));
        if (legacyVDA) {
            if (CMFormatDescriptionGetMediaSubType(format) != kCMVideoCodecType_H264) {
                CFRelease(sample);
                [reader cancelReading];
                return report(result, @"vda-format", -1);
            }
            NSDictionary* extensions = (__bridge NSDictionary*)CMFormatDescriptionGetExtensions(format);
            NSData* avcC = extensions[(__bridge NSString*)kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms]
                                   [@"avcC"];
            if (![avcC isKindOfClass:[NSData class]]) {
                CFRelease(sample);
                [reader cancelReading];
                return report(result, @"vda-avcc", -1);
            }
            NSDictionary* configuration = @{
                (__bridge NSString*)kVDADecoderConfiguration_Width: @(dimensions.width),
                (__bridge NSString*)kVDADecoderConfiguration_Height: @(dimensions.height),
                (__bridge NSString*)kVDADecoderConfiguration_SourceFormat: @(kCMVideoCodecType_H264),
                (__bridge NSString*)kVDADecoderConfiguration_avcCData: avcC,
            };
            DecodeState state = {.digest = 14695981039346656037ULL,
                                 .width = (size_t)dimensions.width, .height = (size_t)dimensions.height};
            VDADecoder decoder = NULL;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
            OSStatus status = VDADecoderCreate((__bridge CFDictionaryRef)configuration, NULL,
                                              (VDADecoderOutputCallback*)vdaDecoded, &state, &decoder);
            if (decoder) { VDADecoderDestroy(decoder); }
#pragma clang diagnostic pop
            CFRelease(sample);
            [reader cancelReading];
            result[@"legacyVDA"] = @YES;
            return report(result, @"vda-create", status);
        }
        NSDictionary* spec = hardware ? @{
            (__bridge NSString*)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder: @YES,
        } : @{
            (__bridge NSString*)kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder: @NO,
        };
        NSDictionary* attributes = @{
            (__bridge NSString*)kCVPixelBufferPixelFormatTypeKey:
                @(tenBit ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
                         : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
        };
        DecodeState state = {.digest = 14695981039346656037ULL,
                             .width = (size_t)dimensions.width, .height = (size_t)dimensions.height};
        VTDecompressionOutputCallbackRecord callback = {decoded, &state};
        VTDecompressionSessionRef session = NULL;
        OSStatus status = VTDecompressionSessionCreate(kCFAllocatorDefault, format, (__bridge CFDictionaryRef)spec,
                                                     (__bridge CFDictionaryRef)attributes, &callback, &session);
        if (status) {
            CFRelease(sample);
            [reader cancelReading];
            return report(result, @"session-create", status);
        }
        CFTypeRef usingHardware = NULL;
        status = VTSessionCopyProperty(session, kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
                                       kCFAllocatorDefault, &usingHardware);
        BOOL active = usingHardware && CFEqual(usingHardware, kCFBooleanTrue);
        result[@"hardwarePropertyStatus"] = @(status);
        result[@"usingHardware"] = status ? (id)[NSNull null] : @(active);
        if (usingHardware) { CFRelease(usingHardware); }
        // 软件解码器可能不实现此可选属性；请求已明确关闭硬解，仍须通过实际帧解码。
        if (!hardware && status == kVTPropertyNotSupportedErr) { status = 0; }
        if (!status && active != hardware) { status = -3; }
        unsigned submitted = 0;
        NSString* stage = @"hardware-property";
        if (!status) {
            stage = @"decode";
            do {
                status = VTDecompressionSessionDecodeFrame(session, sample, 0, NULL, NULL);
                CFRelease(sample);
                sample = NULL;
                submitted++;
                if (status) { break; }
                sample = [output copyNextSampleBuffer];
                while (sample && !CMSampleBufferGetFormatDescription(sample)
                       && CMSampleBufferGetNumSamples(sample) == 0 && markers < 8) {
                    CFRelease(sample);
                    sample = [output copyNextSampleBuffer];
                    markers++;
                }
            } while (sample && submitted < 120);
            if (sample) { CFRelease(sample); }
            OSStatus finish = VTDecompressionSessionFinishDelayedFrames(session);
            OSStatus wait = VTDecompressionSessionWaitForAsynchronousFrames(session);
            if (!status) { status = finish ? finish : wait; }
            if (!status) { status = atomic_load(&state.error); }
            if (!status && reader.status == AVAssetReaderStatusFailed) { status = (OSStatus)reader.error.code; }
            if (!status && atomic_load(&state.frames) != submitted) { status = -4; }
        }
        else { CFRelease(sample); }
        VTDecompressionSessionInvalidate(session);
        CFRelease(session);
        [reader cancelReading];
        result[@"submittedFrames"] = @(submitted);
        result[@"decodedFrames"] = @(atomic_load(&state.frames));
        result[@"pixelDigest"] = [NSString stringWithFormat:@"%016llx", (unsigned long long)state.digest];
        return report(result, stage, status);
    }
}
