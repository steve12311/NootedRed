// Darwin 27 Metal -> Sequoia AMD X5000 descriptor ABI adapter.
// Source layouts verified against both sealed shared caches; never alters
// framework-owned storage or returns legacy bytes to modern framework callers.
#include "DescriptorABI.h"
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#import <objc/runtime.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/sysctl.h>
static _Atomic(uintptr_t) driverStart, driverEnd;
static _Atomic(unsigned long) colorCount, pipelineCount;
static void *(*originalColor)(id, SEL), *(*originalPipeline)(id, SEL);
static char colorKey, pipelineKey;
static int testMode;
static _Atomic(bool) ambiguousDriver;
#ifdef NRMC_TEST
static uint64_t colorTo27(uint64_t x) {
    return nrMCField(x, 0, 1) | (nrMCField(x, 1, 26) << 2) | (nrMCField(x, 27, 4) << 32) |
           (nrMCField(x, 36, 22) << 42);
}
static uint64_t pipelineTo27(uint64_t x) {
    return nrMCField(x, 0, 1) | (nrMCField(x, 1, 1) << 2) | (nrMCField(x, 2, 25) << 4) |
           (nrMCField(x, 27, 9) << 32);
}
#endif
static BOOL fromDriver(void *caller) {
    uintptr_t p = (uintptr_t)caller;
    return !atomic_load(&ambiguousDriver) && p >= atomic_load(&driverStart) && p < atomic_load(&driverEnd);
}
static void *shadowFor(id obj, const void *key, size_t size) {
    // Object-owned, per-thread storage keeps returned pointers valid for the
    // descriptor's lifetime and avoids sharing writable shadows across threads.
    uint64_t tid = 0;
    if (pthread_threadid_np(NULL, &tid))
        tid = (uintptr_t)pthread_self();
    @synchronized(obj) {
        NSMutableDictionary *store = objc_getAssociatedObject(obj, key);
        if (!store) {
            store = [NSMutableDictionary new];
            objc_setAssociatedObject(obj, key, store, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        }
        NSNumber *t = @(tid);
        NSMutableData *data = store[t];
        if (!data) {
            data = [NSMutableData dataWithLength:size];
            store[t] = data;
        }
        return data.mutableBytes;
    }
}
__attribute__((noinline)) static void *colorPrivate(id self, SEL sel) {
    void *raw = originalColor(self, sel);
    if (!raw || !fromDriver(__builtin_return_address(0)))
        return raw;
    uint64_t x;
    memcpy(&x, raw, 8);
    uint64_t observed = x;
#ifdef NRMC_TEST
    x = colorTo27(x);
#endif
    uint64_t value = x;
    if (testMode != 1)
        value = nrMCColorToLegacy(x);
    void *out = shadowFor(self, &colorKey, 8);
    memcpy(out, &value, 8);
    unsigned long n = atomic_fetch_add(&colorCount, 1);
    if (n < 2)
        fprintf(stderr, "NRedMetalCompat color raw=%llx legacy=%llx\n", (unsigned long long)observed,
                (unsigned long long)value);
    return out;
}
__attribute__((noinline)) static void *pipelinePrivate(id self, SEL sel) {
    void *raw = originalPipeline(self, sel);
    if (!raw || !fromDriver(__builtin_return_address(0)))
        return raw;
    void *out = shadowFor(self, &pipelineKey, NRMC_PIPELINE_PRIVATE_SIZE);
    memcpy(out, raw, NRMC_PIPELINE_PRIVATE_SIZE);
    uint64_t x;
    memcpy(&x, (char *)raw + 0xe0, 8);
    uint64_t observed = x;
#ifdef NRMC_TEST
    x = pipelineTo27(x);
#endif
    uint64_t value = x;
    if (testMode != 1)
        value = nrMCPipelineToLegacy(x);
    memcpy((char *)out + 0xe0, &value, 8);
    unsigned long n = atomic_fetch_add(&pipelineCount, 1);
    if (n < 2)
        fprintf(stderr, "NRedMetalCompat pipeline raw=%llx legacy=%llx\n", (unsigned long long)observed,
                (unsigned long long)value);
    return out;
}
static void imageAdded(const struct mach_header *header, intptr_t slide) {
    if (header->magic != MH_MAGIC_64)
        return;
    const struct mach_header_64 *h = (const void *)header;
    const struct load_command *lc = (const void *)(h + 1);
    const char *limit = (const char *)(h + 1) + h->sizeofcmds;
    uintptr_t start = 0, end = 0;
    uint32_t platform = 0, minOS = 0;
    const char *installName = NULL;
    for (unsigned i = 0; i < h->ncmds; i++) {
        if ((const char *)lc + sizeof(*lc) > limit || lc->cmdsize < sizeof(*lc) ||
            lc->cmdsize > (size_t)(limit - (const char *)lc))
            return;
        if (lc->cmd == LC_BUILD_VERSION && lc->cmdsize >= sizeof(struct build_version_command)) {
            const struct build_version_command *b = (const void *)lc;
            platform = b->platform;
            minOS = b->minos;
        }
        if (lc->cmd == LC_ID_DYLIB && lc->cmdsize >= sizeof(struct dylib_command)) {
            const struct dylib_command *d = (const void *)lc;
            uint32_t offset = d->dylib.name.offset;
            if (offset < sizeof(*d) || offset >= lc->cmdsize)
                return;
            const char *name = (const char *)lc + offset;
            if (!memchr(name, 0, lc->cmdsize - offset))
                return;
            installName = name;
        }
        if (lc->cmd == LC_SEGMENT_64 && lc->cmdsize >= sizeof(struct segment_command_64)) {
            const struct segment_command_64 *seg = (const void *)lc;
            if (!strncmp(seg->segname, "__TEXT", 16)) {
                start = seg->vmaddr + slide;
                end = start + seg->vmsize;
            }
        }
        lc = (const void *)((const char *)lc + lc->cmdsize);
    }
    if (nrMCDonorProfile(h->cputype, platform, minOS, installName) && start && end > start) {
        uintptr_t previous = atomic_load(&driverStart);
        if (previous && previous != start) {
            atomic_store(&ambiguousDriver, true);
            return;
        }
        atomic_store(&driverEnd, end);
        atomic_store(&driverStart, start);
    }
}
static void imageRemoved(const struct mach_header *header, intptr_t slide) {
    (void)slide;
    if ((uintptr_t)header == atomic_load(&driverStart)) {
        atomic_store(&driverStart, 0);
        atomic_store(&driverEnd, 0);
    }
}

static BOOL frameworkProfile(Class colorClass, Class pipelineClass, Method colorMethod, Method pipelineMethod,
                             unsigned major) {
    Ivar colorIvar = class_getInstanceVariable(colorClass, "_private");
    Ivar pipelineIvar = class_getInstanceVariable(pipelineClass, "_private");
    if (!colorIvar || !pipelineIvar)
        return NO;
    ptrdiff_t colorOffset = ivar_getOffset(colorIvar), pipelineOffset = ivar_getOffset(pipelineIvar);
    size_t colorTotal = class_getInstanceSize(colorClass),
           pipelineTotal = class_getInstanceSize(pipelineClass);
    if (colorOffset < 0 || pipelineOffset < 0 || (size_t)colorOffset > colorTotal ||
        (size_t)pipelineOffset > pipelineTotal)
        return NO;
    if (colorTotal - (size_t)colorOffset != 8)
        return NO;
#ifdef NRMC_TEST
    if (pipelineTotal - (size_t)pipelineOffset != 632)
        return NO;
#else
    if (pipelineTotal - (size_t)pipelineOffset != 736)
        return NO;
#endif
    @autoreleasepool {
        // Public setters form an independent sample of the private layout.
        id color = [[colorClass alloc] init];
        id pipeline = [[pipelineClass alloc] init];
        [color setValue:@70 forKey:@"pixelFormat"];
        [color setValue:@15 forKey:@"writeMask"];
        [color setValue:@NO forKey:@"blendingEnabled"];
        [color setValue:@0 forKey:@"rgbBlendOperation"];
        [color setValue:@0 forKey:@"alphaBlendOperation"];
        [color setValue:@1 forKey:@"sourceRGBBlendFactor"];
        [color setValue:@1 forKey:@"sourceAlphaBlendFactor"];
        [color setValue:@0 forKey:@"destinationRGBBlendFactor"];
        [color setValue:@0 forKey:@"destinationAlphaBlendFactor"];
        void *(*getColor)(id, SEL) = (void *)method_getImplementation(colorMethod);
        void *(*getPipeline)(id, SEL) = (void *)method_getImplementation(pipelineMethod);
        SEL selector = sel_registerName("_descriptorPrivate");
        uint64_t sample, off, on;
        void *cp = getColor(color, selector), *pp = getPipeline(pipeline, selector);
        if (cp != (char *)(__bridge void *)color + colorOffset ||
            pp != (char *)(__bridge void *)pipeline + pipelineOffset)
            return NO;
        memcpy(&sample, cp, 8);
        [pipeline setValue:@NO forKey:@"rasterizationEnabled"];
        memcpy(&off, (char *)pp + NRMC_PIPELINE_FLAGS_OFFSET, 8);
        [pipeline setValue:@YES forKey:@"rasterizationEnabled"];
        memcpy(&on, (char *)pp + NRMC_PIPELINE_FLAGS_OFFSET, 8);
        // A non-default blend sample distinguishes factor/operation packing,
        // not just the default write mask and pixel format.
        [color setValue:@YES forKey:@"blendingEnabled"];
        [color setValue:@1 forKey:@"rgbBlendOperation"];
        [color setValue:@2 forKey:@"alphaBlendOperation"];
        [color setValue:@4 forKey:@"sourceRGBBlendFactor"];
        [color setValue:@1 forKey:@"sourceAlphaBlendFactor"];
        [color setValue:@5 forKey:@"destinationRGBBlendFactor"];
        [color setValue:@10 forKey:@"writeMask"];
        [color setValue:@80 forKey:@"pixelFormat"];
        uint64_t blend;
        memcpy(&blend, cp, 8);
        [pipeline setValue:@YES forKey:@"alphaToCoverageEnabled"];
        [pipeline setValue:@YES forKey:@"alphaToOneEnabled"];
        [pipeline setValue:@3 forKey:@"inputPrimitiveTopology"];
        uint64_t flags;
        memcpy(&flags, (char *)pp + NRMC_PIPELINE_FLAGS_OFFSET, 8);
#ifdef NRMC_TEST
        const uint64_t expectedBlend = 1 | (UINT64_C(1) << 1) | (UINT64_C(2) << 4) | (UINT64_C(4) << 7) |
                                       (UINT64_C(1) << 12) | (UINT64_C(5) << 17) | (UINT64_C(10) << 27) |
                                       (UINT64_C(80) << 36);
        if (blend != expectedBlend || (flags & 31) != 31)
            return NO;
#else
        const uint64_t expectedBlend = 1 | (UINT64_C(1) << 2) | (UINT64_C(2) << 5) | (UINT64_C(4) << 8) |
                                       (UINT64_C(1) << 13) | (UINT64_C(5) << 18) | (UINT64_C(10) << 32) |
                                       (UINT64_C(80) << 42);
        if (blend != expectedBlend || (flags & 127) != 117)
            return NO;
#endif
#ifdef NRMC_TEST
        return major == 24 && pipelineTotal - (size_t)pipelineOffset == 632 &&
               colorTotal - (size_t)colorOffset == 8 && sample == UINT64_C(0x46078001080) && !(off & 4) &&
               (on ^ off) == 4;
#else
        return nrMCFrameworkProfile(major, pipelineTotal - (size_t)pipelineOffset,
                                    colorTotal - (size_t)colorOffset, sample, off, on);
#endif
    }
}
__attribute__((visibility("default"))) void NRedMetalCompatStats(unsigned long *colors,
                                                                 unsigned long *pipelines) {
    *colors = atomic_load(&colorCount);
    *pipelines = atomic_load(&pipelineCount);
}
__attribute__((constructor)) static void install(void) {
    char release[64] = {0};
    size_t n = sizeof(release);
    if (sysctlbyname("kern.osrelease", release, &n, NULL, 0))
        return;
    unsigned major = (unsigned)strtoul(release, NULL, 10);
#ifdef NRMC_TEST
    const char *mode = getenv("NRMC_TEST_MODE");
    if (major != 24 || !mode)
        return;
    testMode = atoi(mode);
    if (testMode != 1 && testMode != 2)
        return;
#else
    if (major != 27)
        return;
#endif
    SEL sel = sel_registerName("_descriptorPrivate");
    Class color = objc_getClass("MTLRenderPipelineColorAttachmentDescriptorInternal"),
          pipeline = objc_getClass("MTLRenderPipelineDescriptorInternal");
    Method cm = class_getInstanceMethod(color, sel), pm = class_getInstanceMethod(pipeline, sel);
    if (!cm || !pm) {
        fputs("NRedMetalCompat descriptor methods absent; adapter inactive\n", stderr);
        return;
    }
    @try {
        if (!frameworkProfile(color, pipeline, cm, pm, major)) {
            fputs("NRedMetalCompat unsupported descriptor layout; inactive\n", stderr);
            return;
        }
    } @catch (NSException *exception) {
        (void)exception;
        fputs("NRedMetalCompat layout probe failed; inactive\n", stderr);
        return;
    }
    // Objective-C retains the wrapper IMPs even after a client closes a donor
    // handle. Keep this helper mapped for the lifetime of the process.
    Dl_info ownImage = {0};
    if (!dladdr((const void *)install, &ownImage) || !ownImage.dli_fname ||
        !dlopen(ownImage.dli_fname, RTLD_LAZY | RTLD_NOLOAD | RTLD_NODELETE)) {
        fputs("NRedMetalCompat cannot retain adapter image; inactive\n", stderr);
        return;
    }
    _dyld_register_func_for_add_image(imageAdded);
    _dyld_register_func_for_remove_image(imageRemoved);
    originalColor = (void *)method_setImplementation(cm, (IMP)colorPrivate);
    originalPipeline = (void *)method_setImplementation(pm, (IMP)pipelinePrivate);
    fputs("NRedMetalCompat descriptor ABI adapter installed (Sequoia AMD X5000 callers only)\n", stderr);
}
