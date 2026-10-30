"""在 CPU 上执行真实回调，检查 x420 样本读取和完整平面摘要，不创建编解码会话。"""
from pathlib import Path
import subprocess
import plistlib
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/video-main10-regression'


class Main10Tests(unittest.TestCase):
    def test_personality_encode_version_matches_supported_profiles(self):
        xml = (ROOT / 'NootedRed/Personalities/com.apple.kext.AMDRadeonX5000.xml').read_bytes()
        profiles = plistlib.loads(b'<plist version="1.0">' + xml + b'</plist>')
        for profile in profiles:
            devices = profile['IOPCIMatch']
            is_vcn2 = '0x16381002' in devices
            self.assertEqual(profile['IOGVAHEVCEncode'], '2' if is_vcn2 else '1', devices)
            expected = [1, 2] if is_vcn2 else [1]
            self.assertEqual(profile['IOGVAHEVCEncodeCapabilities']['VTSupportedProfileArray'], expected, devices)

    def run_native(self, name, source):
        WORK.mkdir(parents=True, exist_ok=True)
        path, executable = WORK / f'{name}.m', WORK / name
        path.write_text(source)
        subprocess.run([
            'xcrun', 'clang', '-std=c11', '-fobjc-arc', '-Wno-deprecated-declarations',
            '-Wno-unused-parameter', '-I' + str(ROOT / 'tools'), str(path),
            '-framework', 'Foundation', '-framework', 'AVFoundation', '-framework', 'VideoToolbox',
            '-framework', 'CoreMedia', '-framework', 'CoreVideo', '-framework', 'VideoDecodeAcceleration',
            '-o', str(executable),
        ], check=True, timeout=60)
        subprocess.run([str(executable)], check=True, timeout=60)

    def test_x420_reads_all_ten_bits(self):
        self.run_native('samples', r'''
#define main encodeToolMain
#include "test_video_encode.m"
#undef main
#include <assert.h>
int main(void) {
    for (unsigned value = 0; value < 1024; ++value) {
        uint16_t raw = (uint16_t)(value << 6);
        assert(sample16((const uint8_t *)&raw, 0) == (int)value);
    }
    return 0;
}
''')

    def test_digest_covers_each_chroma_component_and_ignores_padding(self):
        self.run_native('digest', r'''
#define main decodeToolMain
#include "test_video_decode.m"
#undef main
#include <assert.h>
static uint64_t digest(CVPixelBufferRef pixel) {
    DecodeState state = {.digest = 14695981039346656037ULL, .width = 8, .height = 4};
    decoded(&state, NULL, 0, 0, pixel, kCMTimeZero, kCMTimeZero);
    assert(atomic_load(&state.error) == 0 && atomic_load(&state.frames) == 1);
    return state.digest;
}
int main(void) {
    @autoreleasepool {
        for (unsigned bits = 8; bits <= 10; bits += 2) {
            tenBit = bits == 10;
            CVPixelBufferRef pixel = NULL;
            OSType format = tenBit ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
                                  : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
            assert(CVPixelBufferCreate(NULL, 8, 4, format, NULL, &pixel) == 0);
            assert(CVPixelBufferLockBaseAddress(pixel, 0) == 0);
            for (size_t plane = 0; plane < 2; ++plane)
                memset(CVPixelBufferGetBaseAddressOfPlane(pixel, plane), 0,
                       CVPixelBufferGetBytesPerRowOfPlane(pixel, plane)
                       * CVPixelBufferGetHeightOfPlane(pixel, plane));
            CVPixelBufferUnlockBaseAddress(pixel, 0);
            uint64_t original = digest(pixel);
            size_t bytes = 8 * (tenBit ? 2 : 1);
            for (size_t y = 0; y < 2; ++y) for (size_t x = 0; x < bytes; ++x) {
                assert(CVPixelBufferLockBaseAddress(pixel, 0) == 0);
                uint8_t *row = (uint8_t *)CVPixelBufferGetBaseAddressOfPlane(pixel, 1)
                               + y * CVPixelBufferGetBytesPerRowOfPlane(pixel, 1);
                row[x] = 1;
                CVPixelBufferUnlockBaseAddress(pixel, 0);
                assert(digest(pixel) != original);
                assert(CVPixelBufferLockBaseAddress(pixel, 0) == 0);
                row[x] = 0;
                CVPixelBufferUnlockBaseAddress(pixel, 0);
            }
            assert(CVPixelBufferLockBaseAddress(pixel, 0) == 0);
            size_t stride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 1);
            if (stride > bytes)
                ((uint8_t *)CVPixelBufferGetBaseAddressOfPlane(pixel, 1))[bytes] = 1;
            CVPixelBufferUnlockBaseAddress(pixel, 0);
            assert(digest(pixel) == original);
            CVPixelBufferRelease(pixel);
        }
    }
    return 0;
}
''')


if __name__ == '__main__':
    unittest.main()
