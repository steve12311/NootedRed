// 使用本地生成的 NV12 帧检查编码器选择和真实输出，不读取用户媒体。
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>
#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef NRED_ENCODE_WIDTH
#define NRED_ENCODE_WIDTH 1280
#endif
#ifndef NRED_ENCODE_HEIGHT
#define NRED_ENCODE_HEIGHT 720
#endif
#ifndef NRED_ENCODE_FRAMES
#define NRED_ENCODE_FRAMES 3
#endif
#if NRED_ENCODE_WIDTH < 16 || NRED_ENCODE_WIDTH > 4096 || NRED_ENCODE_WIDTH % 2 \
    || NRED_ENCODE_HEIGHT < 16 || NRED_ENCODE_HEIGHT > 2160 || NRED_ENCODE_HEIGHT % 2 \
    || NRED_ENCODE_FRAMES < 1 || NRED_ENCODE_FRAMES > 120
#error Invalid synthetic encoding dimensions or frame count
#endif
enum { FrameWidth = NRED_ENCODE_WIDTH, FrameHeight = NRED_ENCODE_HEIGHT, FrameCount = NRED_ENCODE_FRAMES };

// Main10 模式使用 x420（10 位视频范围）输入与输出，样本按 P010 布局存放在 16 位字的高 10 位。
static BOOL main10;
static OSType sourceFormat(void) {
    return main10 ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
                  : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
}
static int sample16(const uint8_t *row, size_t index) {
    uint16_t raw;
    memcpy(&raw, row + index * 2, sizeof(raw));
    return (int)(raw >> 6);
}

static FILE *eventStream;
static void closeEventStream(void) { if (eventStream) fclose(eventStream); }

typedef struct {
    atomic_uint frames;
    atomic_ullong bytes;
    atomic_int error;
    CMSampleBufferRef samples[FrameCount];
} EncodeState;

static void emit(NSDictionary *row) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:row options:NSJSONWritingSortedKeys error:&error];
    if (!data) {
        fprintf(stderr, "JSON serialization failed: %s\n", error.description.UTF8String);
        exit(2);
    }
    fwrite(data.bytes, 1, data.length, eventStream);
    fputc('\n', eventStream);
    fflush(eventStream);
}

static void encoded(void *refcon, void *source, OSStatus status, VTEncodeInfoFlags flags,
                    CMSampleBufferRef sample) {
    (void)source;
    EncodeState *state = refcon;
    if (status != noErr) {
        atomic_store(&state->error, status);
        return;
    }
    if ((flags & kVTEncodeInfo_FrameDropped) || !sample || !CMSampleBufferDataIsReady(sample)
        || !CMSampleBufferGetDataBuffer(sample)) {
        atomic_store(&state->error, kVTVideoEncoderMalfunctionErr);
        return;
    }
    size_t size = CMBlockBufferGetDataLength(CMSampleBufferGetDataBuffer(sample));
    if (!size) {
        atomic_store(&state->error, kVTVideoEncoderMalfunctionErr);
        return;
    }
    unsigned index = atomic_fetch_add(&state->frames, CMSampleBufferGetNumSamples(sample));
    if (index < FrameCount) state->samples[index] = (CMSampleBufferRef)CFRetain(sample);
    atomic_fetch_add(&state->bytes, size);
}

typedef struct {
    atomic_uint frames;
    atomic_int error;
    atomic_ullong lumaError;
    atomic_ullong pixels;
    atomic_ullong chromaError;
    atomic_ullong chromaSamples;
    atomic_uint seen[FrameCount];
} DecodeState;

static void decoded(void *refcon, void *source, OSStatus status, VTDecodeInfoFlags flags,
                    CVImageBufferRef image, CMTime pts, CMTime duration) {
    (void)source;
    (void)duration;
    DecodeState *state = refcon;
    if (status != noErr || !image || (flags & kVTDecodeInfo_FrameDropped)
        || CVPixelBufferGetWidth(image) != FrameWidth || CVPixelBufferGetHeight(image) != FrameHeight
        || CVPixelBufferGetPixelFormatType(image) != sourceFormat()) {
        atomic_store(&state->error, status != noErr ? status : kVTVideoDecoderBadDataErr);
        return;
    }
    if (!CMTIME_IS_NUMERIC(pts)) {
        atomic_store(&state->error, kVTVideoDecoderBadDataErr);
        return;
    }
    int frame = (int)llround(CMTimeGetSeconds(pts) * 30.0);
    if (frame < 0 || frame >= FrameCount) {
        atomic_store(&state->error, kVTVideoDecoderBadDataErr);
        return;
    }
    if (atomic_fetch_add(&state->seen[frame], 1) != 0) {
        atomic_store(&state->error, kVTVideoDecoderBadDataErr);
        return;
    }
    status = CVPixelBufferLockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
    if (status != noErr) {
        atomic_store(&state->error, status);
        return;
    }
    const uint8_t *luma = CVPixelBufferGetBaseAddressOfPlane(image, 0);
    size_t stride = CVPixelBufferGetBytesPerRowOfPlane(image, 0);
    unsigned long long error = 0;
    const int scale = main10 ? 4 : 1;
    for (int y = 0; y < FrameHeight; ++y)
        for (int x = 0; x < FrameWidth; ++x) {
            const int expected = (16 + ((x + y + frame * 19) % 220)) * scale;
            const int sample = main10 ? sample16(luma + y * stride, (size_t)x) : luma[y * stride + x];
            error += abs(sample - expected);
        }
    const uint8_t *chroma = CVPixelBufferGetBaseAddressOfPlane(image, 1);
    size_t chromaStride = CVPixelBufferGetBytesPerRowOfPlane(image, 1);
    unsigned long long chromaError = 0;
    for (int y = 0; y < FrameHeight / 2; ++y)
        for (int x = 0; x < FrameWidth / 2; ++x) {
            const int expectedU = (64 + ((x + y + frame * 13) % 128)) * scale;
            const int expectedV = (64 + ((x * 3 + y * 2 + frame * 7) % 128)) * scale;
            int sampleU, sampleV;
            if (main10) {
                sampleU = sample16(chroma + y * chromaStride, (size_t)x * 2);
                sampleV = sample16(chroma + y * chromaStride, (size_t)x * 2 + 1);
            } else {
                sampleU = chroma[y * chromaStride + x * 2];
                sampleV = chroma[y * chromaStride + x * 2 + 1];
            }
            chromaError += abs(sampleU - expectedU);
            chromaError += abs(sampleV - expectedV);
        }
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
    atomic_fetch_add(&state->lumaError, error);
    atomic_fetch_add(&state->pixels, (unsigned long long)FrameWidth * FrameHeight);
    atomic_fetch_add(&state->chromaError, chromaError);
    atomic_fetch_add(&state->chromaSamples, (unsigned long long)FrameWidth * FrameHeight / 2);
    atomic_fetch_add(&state->frames, 1);
}

// 从格式描述中的 SPS 读取 general_profile_idc 与 bit_depth_luma_minus8，
// 防止“请求 Main10、实际输出 Main 8 位”被误判为通过。
typedef struct {
    int profileIdc;
    int bitDepthLumaMinus8;
} SpsInfo;

static int bitsGet(const uint8_t *data, size_t bytes, size_t *bit, unsigned count) {
    unsigned value = 0;
    for (unsigned i = 0; i < count; ++i) {
        if (*bit >= bytes * 8) return -1;
        value = (value << 1) | ((data[*bit >> 3] >> (7 - (*bit & 7))) & 1U);
        ++*bit;
    }
    return (int)value;
}

static int ueRead(const uint8_t *data, size_t bytes, size_t *bit) {
    int zeros = 0;
    int b = 0;
    do {
        if (*bit >= bytes * 8 || zeros > 30) return -1;
        b = (data[*bit >> 3] >> (7 - (*bit & 7))) & 1;
        ++*bit;
        if (!b) ++zeros;
    } while (!b);
    const int rest = zeros ? bitsGet(data, bytes, bit, (unsigned)zeros) : 0;
    if (rest < 0) return -1;
    return (int)((1ULL << zeros) - 1 + (unsigned)rest);
}

static bool parseHevcSps(CMFormatDescriptionRef format, SpsInfo *out) {
    out->profileIdc = -1;
    out->bitDepthLumaMinus8 = -1;
    if (!format || CMFormatDescriptionGetMediaSubType(format) != kCMVideoCodecType_HEVC) return false;
    size_t count = 0;
    int nalLength = 0;
    if (CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(format, 0, NULL, NULL, &count, &nalLength) != noErr
        || count == 0) return false;
    for (size_t index = 0; index < count; ++index) {
        const uint8_t *ps = NULL;
        size_t size = 0;
        if (CMVideoFormatDescriptionGetHEVCParameterSetAtIndex(format, index, &ps, &size, &count, &nalLength) != noErr
            || !ps || size < 4 || ((ps[0] >> 1) & 0x3F) != 33) continue;
        uint8_t rbsp[2048];
        size_t n = 0;
        int zeros = 0;
        for (size_t i = 0; i < size && n < sizeof(rbsp); ++i) {
            if (zeros >= 2 && ps[i] == 3) { zeros = 0; continue; }
            rbsp[n++] = ps[i];
            zeros = ps[i] == 0 ? zeros + 1 : 0;
        }
        size_t bit = 16;    // 跳过 2 字节 NAL 头
        (void)bitsGet(rbsp, n, &bit, 4);    // sps_video_parameter_set_id
        const int maxSubLayers = bitsGet(rbsp, n, &bit, 3);
        if (maxSubLayers < 0) return false;
        (void)bitsGet(rbsp, n, &bit, 1);    // sps_temporal_id_nesting_flag
        (void)bitsGet(rbsp, n, &bit, 2);    // general_profile_space
        (void)bitsGet(rbsp, n, &bit, 1);    // general_tier_flag
        out->profileIdc = bitsGet(rbsp, n, &bit, 5);
        bit += 32 + 48 + 8;    // 兼容标志、约束标志、general_level_idc
        if (maxSubLayers > 0) {
            int present[8] = {0}, level[8] = {0};
            for (int i = 0; i < maxSubLayers; ++i) {
                present[i] = bitsGet(rbsp, n, &bit, 1);
                level[i] = bitsGet(rbsp, n, &bit, 1);
            }
            for (int i = maxSubLayers; i < 8; ++i) (void)bitsGet(rbsp, n, &bit, 2);
            for (int i = 0; i < maxSubLayers; ++i) {
                if (present[i] > 0) bit += 88;
                if (level[i] > 0) bit += 8;
            }
        }
        (void)ueRead(rbsp, n, &bit);    // sps_seq_parameter_set_id
        const int chroma = ueRead(rbsp, n, &bit);
        if (chroma == 3) (void)bitsGet(rbsp, n, &bit, 1);
        (void)ueRead(rbsp, n, &bit);    // pic_width_in_luma_samples
        (void)ueRead(rbsp, n, &bit);    // pic_height_in_luma_samples
        if (bitsGet(rbsp, n, &bit, 1) > 0) {
            for (int i = 0; i < 4; ++i) (void)ueRead(rbsp, n, &bit);
        }
        out->bitDepthLumaMinus8 = ueRead(rbsp, n, &bit);
        return out->profileIdc >= 0 && out->bitDepthLumaMinus8 >= 0;
    }
    return false;
}

static NSDictionary *checkOutput(EncodeState *encodedState) {
    DecodeState state = {0};
    VTDecompressionSessionRef session = NULL;
    OSStatus status = noErr;
    NSDictionary *spec = @{(__bridge id)kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder: @NO};
    NSDictionary *attributes = @{
        (__bridge id)kCVPixelBufferPixelFormatTypeKey: @(sourceFormat())
    };
    VTDecompressionOutputCallbackRecord callback = {decoded, &state};
    emit(@{@"stage": @"verify-output-begin"});
    if (!encodedState->samples[0]) {
        status = kVTVideoDecoderBadDataErr;
    } else {
        status = VTDecompressionSessionCreate(kCFAllocatorDefault,
            CMSampleBufferGetFormatDescription(encodedState->samples[0]), (__bridge CFDictionaryRef)spec,
            (__bridge CFDictionaryRef)attributes, &callback, &session);
    }
    for (int i = 0; status == noErr && i < FrameCount; ++i) {
        if (!encodedState->samples[i]) {
            status = kVTVideoDecoderBadDataErr;
            break;
        }
        status = VTDecompressionSessionDecodeFrame(session, encodedState->samples[i], 0, NULL, NULL);
    }
    if (session) {
        OSStatus finish = VTDecompressionSessionFinishDelayedFrames(session);
        OSStatus wait = VTDecompressionSessionWaitForAsynchronousFrames(session);
        if (status == noErr) status = finish != noErr ? finish : wait;
        VTDecompressionSessionInvalidate(session);
        CFRelease(session);
    }
    SpsInfo sps = {-1, -1};
    if (encodedState->samples[0]) {
        parseHevcSps(CMSampleBufferGetFormatDescription(encodedState->samples[0]), &sps);
    }
    for (int i = 0; i < FrameCount; ++i)
        if (encodedState->samples[i]) CFRelease(encodedState->samples[i]);
    unsigned frames = atomic_load(&state.frames);
    unsigned long long pixels = atomic_load(&state.pixels);
    double meanError = pixels ? (double)atomic_load(&state.lumaError) / pixels : 0;
    unsigned long long chromaSamples = atomic_load(&state.chromaSamples);
    double meanChromaError = chromaSamples ? (double)atomic_load(&state.chromaError) / chromaSamples : 0;
    // 合成梯度的亮度 MAE 上限按位深缩放：8 位为 10，10 位为 40。
    const double limit = main10 ? 40 : 10;
    // Main10 请求必须真正产出 Main10 码流，否则只算“请求被忽略”。
    const bool spsMain10 = !main10 || (sps.profileIdc == 2 && sps.bitDepthLumaMinus8 == 2);
    return @{@"status": @(status), @"callbackError": @(atomic_load(&state.error)),
             @"decodedFrames": @(frames), @"lumaMeanAbsoluteError": pixels ? @(meanError) : (id)[NSNull null],
             @"lumaErrorLimit": @(limit),
             @"chromaMeanAbsoluteError": chromaSamples ? @(meanChromaError) : (id)[NSNull null],
             @"chromaErrorLimit": @(limit),
             @"spsProfileIdc": @(sps.profileIdc), @"spsBitDepthLumaMinus8": @(sps.bitDepthLumaMinus8),
             @"spsMain10": @(spsMain10),
             @"passed": @(status == noErr && atomic_load(&state.error) == noErr && frames == FrameCount
                          && meanError <= limit && meanChromaError <= limit && spsMain10)};
}

static NSDictionary *selection(VTCompressionSessionRef session) {
    NSMutableDictionary *row = [NSMutableDictionary dictionary];
    CFTypeRef hardware = NULL, encoder = NULL;
    OSStatus hardwareStatus = VTSessionCopyProperty(session,
        kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder, kCFAllocatorDefault, &hardware);
    OSStatus encoderStatus = VTSessionCopyProperty(session, kVTCompressionPropertyKey_EncoderID,
                                                   kCFAllocatorDefault, &encoder);
    row[@"hardwarePropertyStatus"] = @(hardwareStatus);
    row[@"encoderPropertyStatus"] = @(encoderStatus);
    row[@"usingHardware"] = hardwareStatus == noErr && hardware && CFGetTypeID(hardware) == CFBooleanGetTypeID()
        ? @(CFBooleanGetValue(hardware)) : (id)[NSNull null];
    row[@"encoderID"] = encoderStatus == noErr && encoder && CFGetTypeID(encoder) == CFStringGetTypeID()
        ? (__bridge id)encoder : (id)[NSNull null];
    if (hardware) CFRelease(hardware);
    if (encoder) CFRelease(encoder);
    return row;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        // 进程内 GVA 也会向 stdout 写诊断信息；独立保留 JSON 输出，原生信息转到 stderr。
        int descriptor = dup(STDOUT_FILENO);
        if (descriptor < 0) return 2;
        eventStream = fdopen(descriptor, "w");
        if (!eventStream) { close(descriptor); return 2; }
        atexit(closeEventStream);
        if (dup2(STDERR_FILENO, STDOUT_FILENO) < 0) return 2;
        if (argc == 2 && strcmp(argv[1], "--list") == 0) {
            CFArrayRef list = NULL;
            emit(@{@"stage": @"list-begin"});
            OSStatus status = VTCopyVideoEncoderList(NULL, &list);
            emit(@{@"stage": @"list", @"status": @(status),
                   @"encoders": list ? (__bridge id)list : @[]});
            if (list) CFRelease(list);
            return status == noErr ? 0 : 1;
        }
        if ((argc != 3 && argc != 4) || (strcmp(argv[1], "--software") && strcmp(argv[1], "--auto")
                         && strcmp(argv[1], "--hardware"))
            || (strcmp(argv[2], "h264") && strcmp(argv[2], "hevc"))
            || (argc == 4 && strcmp(argv[3], "--main10"))) {
            fprintf(stderr, "Usage: %s --list | --software|--auto|--hardware h264|hevc [--main10]\n", argv[0]);
            return 2;
        }
        main10 = argc == 4;
        NSString *mode = [NSString stringWithUTF8String:argv[1] + 2];
        NSString *codec = [NSString stringWithUTF8String:argv[2]];
        CMVideoCodecType type = [codec isEqualToString:@"h264"] ? kCMVideoCodecType_H264 : kCMVideoCodecType_HEVC;
        if (main10 && type != kCMVideoCodecType_HEVC) {
            fprintf(stderr, "Main10 仅适用于 HEVC\n");
            return 2;
        }
        NSMutableDictionary *spec = [NSMutableDictionary dictionary];
        if ([mode isEqualToString:@"software"])
            spec[(__bridge id)kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder] = @NO;
        if ([mode isEqualToString:@"hardware"])
            spec[(__bridge id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder] = @YES;
        const int width = FrameWidth, height = FrameHeight, frameCount = FrameCount;
        NSDictionary *attributes = @{
            (__bridge id)kCVPixelBufferPixelFormatTypeKey: @(sourceFormat()),
            (__bridge id)kCVPixelBufferWidthKey: @(width), (__bridge id)kCVPixelBufferHeightKey: @(height),
            (__bridge id)kCVPixelBufferIOSurfacePropertiesKey: @{}
        };
        EncodeState state = {0};
        VTCompressionSessionRef session = NULL;
        emit(@{@"stage": @"create-begin", @"mode": mode, @"codec": codec,
               @"width": @(width), @"height": @(height)});
        OSStatus status = VTCompressionSessionCreate(kCFAllocatorDefault, width, height, type,
            (__bridge CFDictionaryRef)spec, (__bridge CFDictionaryRef)attributes, NULL, encoded, &state, &session);
        emit(@{@"stage": @"create", @"status": @(status)});
        if (status != noErr || !session) return 1;
        NSMutableDictionary *properties = [@{
            (__bridge id)kVTCompressionPropertyKey_RealTime: @YES,
            (__bridge id)kVTCompressionPropertyKey_AllowFrameReordering: @NO,
            (__bridge id)kVTCompressionPropertyKey_ExpectedFrameRate: @30
        } mutableCopy];
        if (main10) {
            properties[(__bridge id)kVTCompressionPropertyKey_ProfileLevel] =
                (__bridge id)kVTProfileLevel_HEVC_Main10_AutoLevel;
        }
        for (id key in properties) {
            status = VTSessionSetProperty(session, (__bridge CFStringRef)key, (__bridge CFTypeRef)properties[key]);
            emit(@{@"stage": @"property", @"key": key, @"status": @(status)});
            if (status != noErr) break;
        }
        unsigned submitted = 0;
        if (status == noErr) {
            emit(@{@"stage": @"prepare-begin"});
            status = VTCompressionSessionPrepareToEncodeFrames(session);
            emit(@{@"stage": @"prepare", @"status": @(status), @"selection": selection(session)});
        }
        for (int i = 0; status == noErr && i < frameCount; ++i) {
            CVPixelBufferRef pixel = NULL;
            status = CVPixelBufferCreate(kCFAllocatorDefault, width, height,
                sourceFormat(), (__bridge CFDictionaryRef)attributes, &pixel);
            if (status != noErr || !pixel) break;
            status = CVPixelBufferLockBaseAddress(pixel, 0);
            if (status == noErr) {
                uint8_t *luma = CVPixelBufferGetBaseAddressOfPlane(pixel, 0);
                size_t stride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 0);
                uint8_t *chroma = CVPixelBufferGetBaseAddressOfPlane(pixel, 1);
                size_t chromaStride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 1);
                if (main10) {
                    // x420 与 P010 相同：10 位值位于 16 位字的高 10 位。
                    for (int y = 0; y < height; ++y) {
                        uint16_t *row = (uint16_t *)(luma + y * stride);
                        for (int x = 0; x < width; ++x)
                            row[x] = (uint16_t)((uint16_t)((16 + ((x + y + i * 19) % 220)) * 4) << 6);
                    }
                    for (int y = 0; y < height / 2; ++y) {
                        uint16_t *row = (uint16_t *)(chroma + y * chromaStride);
                        for (int x = 0; x < width / 2; ++x) {
                            row[x * 2] = (uint16_t)((uint16_t)((64 + ((x + y + i * 13) % 128)) * 4) << 6);
                            row[x * 2 + 1] = (uint16_t)((uint16_t)((64 + ((x * 3 + y * 2 + i * 7) % 128)) * 4) << 6);
                        }
                    }
                } else {
                    for (int y = 0; y < height; ++y)
                        for (int x = 0; x < width; ++x) luma[y * stride + x] = 16 + ((x + y + i * 19) % 220);
                    memset(chroma, 128, chromaStride * CVPixelBufferGetHeightOfPlane(pixel, 1));
                    for (int y = 0; y < height / 2; ++y)
                        for (int x = 0; x < width / 2; ++x) {
                            chroma[y * chromaStride + x * 2] = 64 + ((x + y + i * 13) % 128);
                            chroma[y * chromaStride + x * 2 + 1] = 64 + ((x * 3 + y * 2 + i * 7) % 128);
                        }
                }
                status = CVPixelBufferUnlockBaseAddress(pixel, 0);
            }
            if (status == noErr) {
                emit(@{@"stage": @"encode-begin", @"frame": @(i)});
                status = VTCompressionSessionEncodeFrame(session, pixel, CMTimeMake(i, 30), CMTimeMake(1, 30),
                                                         NULL, NULL, NULL);
                emit(@{@"stage": @"encode", @"frame": @(i), @"status": @(status)});
                if (status == noErr) ++submitted;
            }
            CVPixelBufferRelease(pixel);
        }
        OSStatus completeStatus = status;
        if (status == noErr) {
            emit(@{@"stage": @"complete-begin"});
            completeStatus = VTCompressionSessionCompleteFrames(session, kCMTimeInvalid);
            emit(@{@"stage": @"complete", @"status": @(completeStatus)});
        }
        NSDictionary *chosen = selection(session);
        emit(@{@"stage": @"invalidate-begin"});
        VTCompressionSessionInvalidate(session);
        CFRelease(session);
        unsigned frames = atomic_load(&state.frames);
        unsigned long long bytes = atomic_load(&state.bytes);
        int callbackError = atomic_load(&state.error);
        id hardware = chosen[@"usingHardware"];
        // 软件实现可不提供硬件状态属性；此时保留 unknown，并核对软件编码器 ID。
        BOOL knownSoftware = [chosen[@"encoderID"] isEqual:@"com.apple.videotoolbox.videoencoder.h264"]
            || [chosen[@"encoderID"] isEqual:@"com.apple.videotoolbox.videoencoder.hevc.vcp"];
        BOOL selectionOK = [mode isEqualToString:@"auto"]
            || ([mode isEqualToString:@"hardware"] && [hardware isEqual:@YES])
            || ([mode isEqualToString:@"software"] && ([hardware isEqual:@NO]
                || (hardware == [NSNull null] && knownSoftware)));
        NSDictionary *verified = checkOutput(&state);
        BOOL passed = status == noErr && completeStatus == noErr && callbackError == noErr
            && submitted == frameCount && frames == frameCount && bytes > 0 && selectionOK
            && [verified[@"passed"] boolValue];
        emit(@{@"stage": @"result", @"mode": mode, @"codec": codec, @"main10": @(main10), @"status": @(status),
               @"completeStatus": @(completeStatus), @"callbackError": @(callbackError),
               @"submittedFrames": @(submitted), @"encodedFrames": @(frames), @"encodedBytes": @(bytes),
               @"selection": chosen, @"outputVerification": verified, @"passed": @(passed)});
        return passed ? 0 : 1;
    }
}
