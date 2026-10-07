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
        || CVPixelBufferGetPixelFormatType(image) != kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange) {
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
    for (int y = 0; y < FrameHeight; ++y)
        for (int x = 0; x < FrameWidth; ++x)
            error += abs((int)luma[y * stride + x] - (16 + ((x + y + frame * 19) % 220)));
    const uint8_t *chroma = CVPixelBufferGetBaseAddressOfPlane(image, 1);
    size_t chromaStride = CVPixelBufferGetBytesPerRowOfPlane(image, 1);
    unsigned long long chromaError = 0;
    for (int y = 0; y < FrameHeight / 2; ++y)
        for (int x = 0; x < FrameWidth / 2; ++x) {
            chromaError += abs((int)chroma[y * chromaStride + x * 2] - (64 + ((x + y + frame * 13) % 128)));
            chromaError += abs((int)chroma[y * chromaStride + x * 2 + 1] - (64 + ((x * 3 + y * 2 + frame * 7) % 128)));
        }
    CVPixelBufferUnlockBaseAddress(image, kCVPixelBufferLock_ReadOnly);
    atomic_fetch_add(&state->lumaError, error);
    atomic_fetch_add(&state->pixels, (unsigned long long)FrameWidth * FrameHeight);
    atomic_fetch_add(&state->chromaError, chromaError);
    atomic_fetch_add(&state->chromaSamples, (unsigned long long)FrameWidth * FrameHeight / 2);
    atomic_fetch_add(&state->frames, 1);
}

static NSDictionary *checkOutput(EncodeState *encodedState) {
    DecodeState state = {0};
    VTDecompressionSessionRef session = NULL;
    OSStatus status = noErr;
    NSDictionary *spec = @{(__bridge id)kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder: @NO};
    NSDictionary *attributes = @{
        (__bridge id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)
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
    for (int i = 0; i < FrameCount; ++i)
        if (encodedState->samples[i]) CFRelease(encodedState->samples[i]);
    unsigned frames = atomic_load(&state.frames);
    unsigned long long pixels = atomic_load(&state.pixels);
    double meanError = pixels ? (double)atomic_load(&state.lumaError) / pixels : 0;
    unsigned long long chromaSamples = atomic_load(&state.chromaSamples);
    double meanChromaError = chromaSamples ? (double)atomic_load(&state.chromaError) / chromaSamples : 0;
    // 合成梯度的 8 位亮度 MAE 上限为 10；拒绝帧数正常但内容损坏的输出。
    return @{@"status": @(status), @"callbackError": @(atomic_load(&state.error)),
             @"decodedFrames": @(frames), @"lumaMeanAbsoluteError": pixels ? @(meanError) : (id)[NSNull null],
             @"lumaErrorLimit": @10,
             @"chromaMeanAbsoluteError": chromaSamples ? @(meanChromaError) : (id)[NSNull null],
             @"chromaErrorLimit": @10,
             @"passed": @(status == noErr && atomic_load(&state.error) == noErr && frames == FrameCount
                          && meanError <= 10 && meanChromaError <= 10)};
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
        if (argc != 3 || (strcmp(argv[1], "--software") && strcmp(argv[1], "--auto")
                         && strcmp(argv[1], "--hardware"))
            || (strcmp(argv[2], "h264") && strcmp(argv[2], "hevc"))) {
            fprintf(stderr, "Usage: %s --list | --software|--auto|--hardware h264|hevc\n", argv[0]);
            return 2;
        }
        NSString *mode = [NSString stringWithUTF8String:argv[1] + 2];
        NSString *codec = [NSString stringWithUTF8String:argv[2]];
        CMVideoCodecType type = [codec isEqualToString:@"h264"] ? kCMVideoCodecType_H264 : kCMVideoCodecType_HEVC;
        NSMutableDictionary *spec = [NSMutableDictionary dictionary];
        if ([mode isEqualToString:@"software"])
            spec[(__bridge id)kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder] = @NO;
        if ([mode isEqualToString:@"hardware"])
            spec[(__bridge id)kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder] = @YES;
        const int width = FrameWidth, height = FrameHeight, frameCount = FrameCount;
        NSDictionary *attributes = @{
            (__bridge id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
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
        NSDictionary *properties = @{
            (__bridge id)kVTCompressionPropertyKey_RealTime: @YES,
            (__bridge id)kVTCompressionPropertyKey_AllowFrameReordering: @NO,
            (__bridge id)kVTCompressionPropertyKey_ExpectedFrameRate: @30
        };
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
                kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange, (__bridge CFDictionaryRef)attributes, &pixel);
            if (status != noErr || !pixel) break;
            status = CVPixelBufferLockBaseAddress(pixel, 0);
            if (status == noErr) {
                uint8_t *luma = CVPixelBufferGetBaseAddressOfPlane(pixel, 0);
                size_t stride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 0);
                for (int y = 0; y < height; ++y)
                    for (int x = 0; x < width; ++x) luma[y * stride + x] = 16 + ((x + y + i * 19) % 220);
                memset(CVPixelBufferGetBaseAddressOfPlane(pixel, 1), 128,
                       CVPixelBufferGetBytesPerRowOfPlane(pixel, 1) * CVPixelBufferGetHeightOfPlane(pixel, 1));
                uint8_t *chroma = CVPixelBufferGetBaseAddressOfPlane(pixel, 1);
                size_t chromaStride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 1);
                for (int y = 0; y < height / 2; ++y)
                    for (int x = 0; x < width / 2; ++x) {
                        chroma[y * chromaStride + x * 2] = 64 + ((x + y + i * 13) % 128);
                        chroma[y * chromaStride + x * 2 + 1] = 64 + ((x * 3 + y * 2 + i * 7) % 128);
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
        emit(@{@"stage": @"result", @"mode": mode, @"codec": codec, @"status": @(status),
               @"completeStatus": @(completeStatus), @"callbackError": @(callbackError),
               @"submittedFrames": @(submitted), @"encodedFrames": @(frames), @"encodedBytes": @(bytes),
               @"selection": chosen, @"outputVerification": verified, @"passed": @(passed)});
        return passed ? 0 : 1;
    }
}
