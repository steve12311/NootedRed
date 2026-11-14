#include "DescriptorABI.h"
#include <assert.h>
#include <stdio.h>

static void observed_failure(void) {
    const uint64_t modern = UINT64_C(0x1180f00002100);
    /* The old driver silently sees a zero color write mask. */
    assert(((modern >> 27) & 15) == 0);
    const uint64_t legacy = nrMCColorToLegacy(modern);
    assert(legacy == UINT64_C(0x46078001080));
    assert(((legacy >> 27) & 15) == 15);
    assert((legacy >> 36) == 70);
    assert(nrMCPipelineToLegacy(UINT64_C(0x1fe0010)) == UINT64_C(0x7f8004));
}

static void distinct_blend_fields(void) {
    /* Independent semantic fixture: enable, RGB subtract, alpha reverse-subtract,
       source RGB alpha, source alpha one, destination RGB one-minus-alpha,
       destination alpha zero, red+blue writes, BGRA8 format. */
    uint64_t modern = 1 | (UINT64_C(1) << 2) | (UINT64_C(2) << 5) | (UINT64_C(4) << 8) | (UINT64_C(1) << 13) |
                      (UINT64_C(5) << 18) | (UINT64_C(10) << 32) | (UINT64_C(80) << 42);
    uint64_t legacy = nrMCColorToLegacy(modern);
    assert((legacy & 1) == 1);
    assert(((legacy >> 1) & 7) == 1);
    assert(((legacy >> 4) & 7) == 2);
    assert(((legacy >> 7) & 31) == 4);
    assert(((legacy >> 12) & 31) == 1);
    assert(((legacy >> 17) & 31) == 5);
    assert(((legacy >> 22) & 31) == 0);
    assert(((legacy >> 27) & 15) == 10);
    assert((legacy >> 36) == 80);
    /* Modern inherited/dynamic blend state is not an enabled legacy boolean. */
    assert((nrMCColorToLegacy((modern & ~UINT64_C(3)) | 2) & 1) == 0);
    assert((nrMCColorToLegacy((modern & ~UINT64_C(3)) | 3) & 1) == 0);
}

static void pipeline_fields(void) {
    uint64_t modern = 1 | (UINT64_C(1) << 2) | (UINT64_C(1) << 4) | (UINT64_C(3) << 5) |
                      (UINT64_C(0xa5) << 17) | (UINT64_C(6) << 25) | (UINT64_C(1) << 28) |
                      (UINT64_C(9) << 32) | (UINT64_C(1) << 36) | (UINT64_C(1) << 37) | (UINT64_C(1) << 38) |
                      (UINT64_C(1) << 40);
    uint64_t legacy = nrMCPipelineToLegacy(modern);
    assert((legacy & 7) == 7);
    assert(((legacy >> 3) & 3) == 3);
    assert(((legacy >> 15) & 255) == 0xa5);
    assert(((legacy >> 23) & 7) == 6);
    assert(((legacy >> 26) & 1) == 1);
    assert(((legacy >> 27) & 15) == 9);
    assert(((legacy >> 31) & 15) == 7);
    assert(((legacy >> 35) & 1) == 1);
}

static void interface_gates(void) {
    const char *name =
        "/System/Library/Extensions/AMDRadeonX5000MTLDriver.bundle/Contents/MacOS/AMDRadeonX5000MTLDriver";
    assert(nrMCDonorProfile(0x1000007, 1, 0x0f0000, name));
    assert(nrMCDonorProfile(0x1000007, 1, 0x0f0800, name));
    assert(!nrMCDonorProfile(0x1000007, 1, 0x100000, name));
    assert(!nrMCDonorProfile(0x100000c, 1, 0x0f0800, name));
    assert(!nrMCDonorProfile(0x1000007, 1, 0x0f0800, "different driver"));
    assert(!nrMCDonorProfile(0x1000007, 1, 0x0f0800, NULL));
    assert(nrMCFrameworkProfile(27, 736, 8, 0x1180f00002100, 0x1fe0000, 0x1fe0010));
    assert(!nrMCFrameworkProfile(26, 736, 8, 0x1180f00002100, 0x1fe0000, 0x1fe0010));
    assert(!nrMCFrameworkProfile(28, 736, 8, 0x1180f00002100, 0x1fe0000, 0x1fe0010));
    assert(!nrMCFrameworkProfile(27, 632, 8, 0x1180f00002100, 0x1fe0000, 0x1fe0010));
    assert(!nrMCFrameworkProfile(27, 736, 8, 0x46078001080, 0x1fe0000, 0x1fe0010));
    assert(!nrMCFrameworkProfile(27, 736, 8, 0x1180f00002100, 0, 4));
}
int main(void) {
    observed_failure();
    distinct_blend_fields();
    pipeline_fields();
    interface_gates();
    puts("PASS: descriptor semantics and interface-profile rejection");
    return 0;
}
