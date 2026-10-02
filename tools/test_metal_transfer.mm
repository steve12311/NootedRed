// 对照 CPU 检查原生 Metal Transfer；默认只检查补丁，不提交 GPU 工作。
// 编译：clang++ -std=c++17 -fobjc-arc -I NootedRed -framework Foundation -framework Metal \
//       -framework VideoToolbox -framework CoreVideo -framework Accelerate \
//       tools/test_metal_transfer.mm -o /tmp/test-metal-transfer
#import <Foundation/Foundation.h>
#import <Accelerate/Accelerate.h>
#import <Metal/Metal.h>
#import <VideoToolbox/VideoToolbox.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <UserSurfaceSyncData.hpp>
#include <dlfcn.h>
#include <mach/mach_vm.h>
#include <cstring>
#include <initializer_list>
#include <utility>
#include <vector>

using CreateTransfer = OSStatus (*)(CFAllocatorRef, id, CFTypeRef*);
using TransferImage = OSStatus (*)(CFTypeRef, CVPixelBufferRef, CVPixelBufferRef, CFDictionaryRef);

struct Sessions {
    CFTypeRef metal{nullptr};
    ~Sessions() {
        if (metal) { CFRelease(metal); }
    }
};

struct Images {
    CVPixelBufferRef source{nullptr}, metal{nullptr}, software{nullptr};
    ~Images() {
        for (auto image : {source, metal, software}) { if (image) { CVPixelBufferRelease(image); } }
    }
};

static bool readBytes(uint64_t address, void* output, size_t size)
{
    mach_vm_size_t copied{0};
    return mach_vm_read_overwrite(mach_task_self(), address, size,
                                  reinterpret_cast<mach_vm_address_t>(output), &copied) == KERN_SUCCESS
        && copied == size;
}

static bool validHost(id<MTLDevice> device)
{
    const auto os = NSProcessInfo.processInfo.operatingSystemVersion;
    if (os.majorVersion != 26 || os.minorVersion != 7 || os.patchVersion != 1 || !device) { return false; }
    SEL infoSelector = sel_registerName("amdMtl_HWL_GetHwInfo");
    SEL sizeSelector = sel_registerName("amdMtl_HWL_GetHwInfoSize");
    if (![device respondsToSelector:infoSelector] || ![device respondsToSelector:sizeSelector]) { return false; }
    auto size = reinterpret_cast<uint32_t (*)(id, SEL)>(objc_msgSend)(device, sizeSelector);
    auto info = reinterpret_cast<const uint8_t* (*)(id, SEL)>(objc_msgSend)(device, infoSelector);
    uint32_t deviceID{0};
    return size == 180 && readBytes(reinterpret_cast<uint64_t>(info) + 12, &deviceID, sizeof(deviceID))
        && deviceID == 0x1638;
}

static int patchState(uint64_t base)
{
    using namespace UserSurfaceSyncData;
    unsigned original{0}, patched{0};
    for (size_t i = 3; i < sizeof(ComputeScratchPatches) / sizeof(ComputeScratchPatches[0]); ++i) {
        const auto& p = ComputeScratchPatches[i];
        uint8_t bytes[MaxPatchSize];
        if (!readBytes(base + p.offset, bytes, p.size)) { return -1; }
        if (!memcmp(bytes, p.original, p.size)) { ++original; }
        else if (!memcmp(bytes, p.patched, p.size)) { ++patched; }
        else { return -1; }
    }
    return original == 1 ? 0 : (patched == 1 ? 1 : -1);
}

static bool patchOwnProcess(uint64_t base)
{
    using namespace UserSurfaceSyncData;
    // 原生函数预检已完成；失败退出测试进程，不在失败后提交 GPU 工作。
    uint64_t pages[1];
    for (unsigned i = 0; i < 1; ++i) {
        pages[i] = (base + ComputeScratchPatches[i + 3].offset) & ~4095ULL;
        if (mach_vm_protect(mach_task_self(), pages[i], 4096, FALSE,
                            VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY) != KERN_SUCCESS) { return false; }
    }
    for (unsigned i = 0; i < 1; ++i) {
        const auto& p = ComputeScratchPatches[i + 3];
        memcpy(reinterpret_cast<void*>(base + p.offset), p.patched, p.size);
    }
    bool restored = true;
    for (auto page : pages) {
        if (mach_vm_protect(mach_task_self(), page, 4096, FALSE, VM_PROT_READ | VM_PROT_EXECUTE) != KERN_SUCCESS) {
            restored = false;
        }
    }
    return restored && patchState(base) == 1;
}

static bool runImage(Sessions& sessions, TransferImage transfer, int width, int height, int outWidth, int outHeight,
                     bool color)
{
    Images images;
    NSDictionary* attributes = @{(id)kCVPixelBufferIOSurfacePropertiesKey: @{},
                                  (id)kCVPixelBufferMetalCompatibilityKey: @YES};
    auto attrs = (__bridge CFDictionaryRef)attributes;
    if (CVPixelBufferCreate(nullptr, width, height, kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange, attrs,
                            &images.source)
        || CVPixelBufferCreate(nullptr, outWidth, outHeight, kCVPixelFormatType_32BGRA, attrs, &images.metal)
        || CVPixelBufferCreate(nullptr, outWidth, outHeight, kCVPixelFormatType_32BGRA, attrs, &images.software)) {
        fprintf(stderr, "创建测试图像失败\n"); return false;
    }
    CVBufferSetAttachment(images.source, kCVImageBufferYCbCrMatrixKey, kCVImageBufferYCbCrMatrix_ITU_R_601_4,
                          kCVAttachmentMode_ShouldPropagate);
    for (auto image : {images.source, images.metal, images.software}) {
        CVBufferSetAttachment(image, kCVImageBufferColorPrimariesKey, kCVImageBufferColorPrimaries_ITU_R_709_2,
                              kCVAttachmentMode_ShouldPropagate);
        CVBufferSetAttachment(image, kCVImageBufferTransferFunctionKey, kCVImageBufferTransferFunction_sRGB,
                              kCVAttachmentMode_ShouldPropagate);
    }
    if (CVPixelBufferLockBaseAddress(images.source, 0)) { return false; }
    for (size_t plane = 0; plane < 2; ++plane) {
        auto* bytes = static_cast<uint8_t*>(CVPixelBufferGetBaseAddressOfPlane(images.source, plane));
        auto stride = CVPixelBufferGetBytesPerRowOfPlane(images.source, plane);
        auto rows = CVPixelBufferGetHeightOfPlane(images.source, plane);
        for (size_t y = 0; y < rows; ++y) {
            memset(bytes + y * stride, plane ? 128 : 16 + (219 * y) / (rows - 1), stride);
            if (plane && color) {
                for (size_t x = 0; x + 1 < stride; x += 2) {
                    bytes[y * stride + x] = 96; bytes[y * stride + x + 1] = 160;
                }
            }
        }
    }
    CVPixelBufferUnlockBaseAddress(images.source, 0);
    // vImage 是独立的 CPU 参考，不依赖 VideoToolbox 私有的诊断属性。
    if (CVPixelBufferLockBaseAddress(images.source, kCVPixelBufferLock_ReadOnly)) { return false; }
    if (CVPixelBufferLockBaseAddress(images.software, 0)) {
        CVPixelBufferUnlockBaseAddress(images.source, kCVPixelBufferLock_ReadOnly); return false;
    }
    vImage_Buffer yp{CVPixelBufferGetBaseAddressOfPlane(images.source, 0), static_cast<size_t>(height),
                    static_cast<size_t>(width), CVPixelBufferGetBytesPerRowOfPlane(images.source, 0)};
    vImage_Buffer cbcr{CVPixelBufferGetBaseAddressOfPlane(images.source, 1), static_cast<size_t>((height + 1) / 2),
                      static_cast<size_t>((width + 1) / 2), CVPixelBufferGetBytesPerRowOfPlane(images.source, 1)};
    vImage_Buffer output{CVPixelBufferGetBaseAddress(images.software), static_cast<size_t>(outHeight),
                        static_cast<size_t>(outWidth), CVPixelBufferGetBytesPerRow(images.software)};
    std::vector<uint8_t> unscaled(static_cast<size_t>(width) * height * 4);
    vImage_Buffer temporary{unscaled.data(), static_cast<size_t>(height), static_cast<size_t>(width),
                           static_cast<size_t>(width) * 4};
    vImage_YpCbCrPixelRange range{16, 128, 235, 240, 255, 0, 255, 0};
    vImage_YpCbCrToARGB conversion;
    const uint8_t permute[] = {3, 2, 1, 0};
    auto cpuStatus = vImageConvert_YpCbCrToARGB_GenerateConversion(kvImage_YpCbCrToARGBMatrix_ITU_R_601_4, &range,
                                                               &conversion, kvImage420Yp8_CbCr8, kvImageARGB8888, 0);
    if (!cpuStatus) {
        cpuStatus = vImageConvert_420Yp8_CbCr8ToARGB8888(&yp, &cbcr, &temporary, &conversion, permute, 255, 0);
    }
    if (!cpuStatus) {
        if (width == outWidth && height == outHeight) {
            for (int y = 0; y < height; ++y) {
                memcpy(static_cast<uint8_t*>(output.data) + y * output.rowBytes,
                       unscaled.data() + y * temporary.rowBytes, temporary.rowBytes);
            }
        } else { cpuStatus = vImageScale_ARGB8888(&temporary, &output, nullptr, kvImageHighQualityResampling); }
    }
    CVPixelBufferUnlockBaseAddress(images.software, 0);
    CVPixelBufferUnlockBaseAddress(images.source, kCVPixelBufferLock_ReadOnly);
    if (cpuStatus) { printf("vImage=%ld\n", cpuStatus); return false; }
    printf("submit=%dx%d->%dx%d color=%d\n", width, height, outWidth, outHeight, color);
    auto status = transfer(sessions.metal, images.source, images.metal, nullptr);
    if (status) { printf("Metal Transfer=%d\n", status); return false; }
    if (CVPixelBufferLockBaseAddress(images.metal, kCVPixelBufferLock_ReadOnly)) { return false; }
    if (CVPixelBufferLockBaseAddress(images.software, kCVPixelBufferLock_ReadOnly)) {
        CVPixelBufferUnlockBaseAddress(images.metal, kCVPixelBufferLock_ReadOnly); return false;
    }
    auto* gpu = static_cast<uint8_t*>(CVPixelBufferGetBaseAddress(images.metal));
    auto* cpu = static_cast<uint8_t*>(CVPixelBufferGetBaseAddress(images.software));
    auto gpuStride = CVPixelBufferGetBytesPerRow(images.metal);
    auto cpuStride = CVPixelBufferGetBytesPerRow(images.software);
    unsigned maxDifference{0};
    size_t overTolerance{0};
    for (int y = 0; y < outHeight; ++y) {
        for (int x = 0; x < outWidth * 4; ++x) {
            auto difference = static_cast<unsigned>(abs(int(gpu[y * gpuStride + x]) - int(cpu[y * cpuStride + x])));
            maxDifference = MAX(maxDifference, difference);
            if (difference > 2) { ++overTolerance; }
        }
    }
    CVPixelBufferUnlockBaseAddress(images.software, kCVPixelBufferLock_ReadOnly);
    CVPixelBufferUnlockBaseAddress(images.metal, kCVPixelBufferLock_ReadOnly);
    printf("maxDifference=%u channelsOverTolerance=%zu\n", maxDifference, overTolerance);
    return overTolerance == 0;
}

int main(int argc, char** argv)
{
    @autoreleasepool {
        setbuf(stdout, nullptr);
        const bool render = argc == 2 && !strcmp(argv[1], "--render");
        const bool candidate = argc == 2 && !strcmp(argv[1], "--candidate-render");
        if (argc > 1 && !render && !candidate && !(argc == 2 && !strcmp(argv[1], "--inspect-only"))) {
            fprintf(stderr, "用法：%s [--inspect-only|--render|--candidate-render]\n", argv[0]); return 2;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!validHost(device)) { fprintf(stderr, "GPU 或系统版本未核实，拒绝测试\n"); return 3; }
        // 核实的 Metal 方法锚点和缓存 UUID 必须同时匹配；不使用固定 ASLR 地址。
        void* library = dlopen("/System/Library/Frameworks/VideoToolbox.framework/VideoToolbox", RTLD_NOW);
        if (!library) { return 4; }
        Method method = class_getInstanceMethod(objc_getClass("GFX9_MtlComputePipelineState"),
                                                sel_registerName("initHwShRegs:"));
        if (!method) { return 4; }
        auto anchor = method_getImplementation(method);
        uint64_t base = reinterpret_cast<uint64_t>(anchor) - (0x7FFB10C7EAC6ULL - UserSurfaceSyncData::CacheBase);
        uint8_t header[104];
        if (!readBytes(base, header, sizeof(header)) || memcmp(header, UserSurfaceSyncData::CacheMagic, 16)
            || memcmp(header + 88, UserSurfaceSyncData::CacheUUID, 16)) { fprintf(stderr, "缓存版本不匹配\n"); return 4; }
        // 原始 kernel 选择必须保持；拒绝与撤回的 1x1 策略混用。
        for (const auto& old : UserSurfaceSyncData::WithdrawnTransferGuards) {
            uint8_t bytes[6];
            if (!readBytes(base + old.offset, bytes, old.size) || memcmp(bytes, old.original, old.size)) {
                fprintf(stderr, "检测到旧 1x1 策略，拒绝混用\n"); return 4;
            }
        }
        int state = patchState(base);
        printf("device=%s computeScratch=%d\n", device.name.UTF8String, state);
        if (state < 0) { return 4; }
        if (!render && !candidate) { return 0; }
        if (candidate && state == 0 && !patchOwnProcess(base)) { return 5; }
        if (patchState(base) != 1) { fprintf(stderr, "补丁未生效，拒绝提交 GPU 工作\n"); return 5; }
        auto create = reinterpret_cast<CreateTransfer>(dlsym(library, "VTMetalTransferSessionCreate"));
        auto transfer = reinterpret_cast<TransferImage>(dlsym(library, "VTMetalTransferSessionTransferImageSync"));
        if (!create || !transfer) { return 6; }
        Sessions sessions;
        auto status = create(kCFAllocatorDefault, device, &sessions.metal);
        if (status || !sessions.metal) { return 6; }
        for (auto size : {std::pair{192, 128}, std::pair{320, 180}, std::pair{640, 360}, std::pair{1920, 1080},
                          std::pair{3072, 1728}, std::pair{258, 130}}) {
            for (bool color : {false, true}) {
                if (!runImage(sessions, transfer, size.first, size.second, size.first, size.second, color)) {
                    return 8;
                }
            }
        }
        // 缩放可能使用其他原生 kernel；单独记录，不把成功的 NV12 转换外推为所有格式支持。
        if (!runImage(sessions, transfer, 320, 180, 192, 128, false)) { return 8; }
        puts("PASS 13 Metal transfers; CPU differences <= 2; no software fallback in Metal test");
        return 0;
    }
}
