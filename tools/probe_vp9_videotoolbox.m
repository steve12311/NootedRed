// VP9 VideoToolbox probe (read-only): reports which VP9 gates VideoToolbox applies on this Mac.
// Build: clang -fobjc-arc -framework VideoToolbox -framework CoreMedia -framework CoreFoundation \
//        -framework Foundation tools/probe_vp9_videotoolbox.m -o build/vp9/probe_vp9_videotoolbox
#import <Foundation/Foundation.h>
#import <VideoToolbox/VideoToolbox.h>
#import <CoreMedia/CoreMedia.h>

extern Boolean VTIsVP9DecodeEnabledForCurrentProcess(void) __attribute__((weak_import));
extern CFDictionaryRef VTCopyVP9DecoderCapabilitiesDictionary(void) __attribute__((weak_import));
extern const CFStringRef kVTVideoDecoderSpecification_AllowVP9Decode __attribute__((weak_import));

static void tryCreate(const char* label, CFDictionaryRef spec)
{
    CMVideoFormatDescriptionRef format = NULL;
    OSStatus status = CMVideoFormatDescriptionCreate(NULL, kCMVideoCodecType_VP9, 1920, 1080, NULL, &format);
    if (status) {
        printf("%s: format description error %d\n", label, (int)status);
        return;
    }
    VTDecompressionSessionRef session = NULL;
    VTDecompressionOutputCallbackRecord callback = {0};
    status = VTDecompressionSessionCreate(NULL, format, spec, NULL, &callback, &session);
    printf("%s: VTDecompressionSessionCreate = %d\n", label, (int)status);
    if (session) {
        CFBooleanRef hw = NULL;
        if (!VTSessionCopyProperty(session, kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
                                   NULL, &hw) && hw) {
            printf("%s: using hardware = %d\n", label, CFBooleanGetValue(hw));
            CFRelease(hw);
        }
        VTDecompressionSessionInvalidate(session);
        CFRelease(session);
    }
    CFRelease(format);
}

int main(void)
{
    @autoreleasepool {
        printf("VTIsHardwareDecodeSupported(VP9) before register = %d\n",
               VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9));
        if (VTIsVP9DecodeEnabledForCurrentProcess)
            printf("VTIsVP9DecodeEnabledForCurrentProcess = %d\n", VTIsVP9DecodeEnabledForCurrentProcess());
        if (VTCopyVP9DecoderCapabilitiesDictionary) {
            CFDictionaryRef caps = VTCopyVP9DecoderCapabilitiesDictionary();
            printf("VTCopyVP9DecoderCapabilitiesDictionary = %s\n",
                   caps ? [[(__bridge NSDictionary*)caps description] UTF8String] : "NULL");
            if (caps)
                CFRelease(caps);
        }
        VTRegisterSupplementalVideoDecoderIfAvailable(kCMVideoCodecType_VP9);
        printf("VTIsHardwareDecodeSupported(VP9) after register = %d\n",
               VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9));
        tryCreate("default", NULL);
        if (kVTVideoDecoderSpecification_AllowVP9Decode) {
            NSDictionary* spec = @{(__bridge NSString*)kVTVideoDecoderSpecification_AllowVP9Decode : @YES};
            tryCreate("AllowVP9Decode", (__bridge CFDictionaryRef)spec);
            NSDictionary* hw = @{
                (__bridge NSString*)kVTVideoDecoderSpecification_AllowVP9Decode : @YES,
                (__bridge NSString*)kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder : @YES
            };
            tryCreate("AllowVP9Decode+RequireHW", (__bridge CFDictionaryRef)hw);
        }
    }
    return 0;
}
