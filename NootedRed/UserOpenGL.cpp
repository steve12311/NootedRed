// 独立 OpenGL 文件在签名验证后修正内存页；不修改磁盘文件或验证结果。
#include <Headers/kern_api.hpp>
#include <Headers/kern_mach.hpp>
#include <Headers/kern_util.hpp>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <UserOpenGL.hpp>
#include <UserOpenGLResolver.hpp>
#include <UserPatchCoordinator.hpp>
#include <VPF/DisplayManifestData.hpp>
#include <VPF/DisplayRuntime.hpp>
#include <VPF/ValidatedPageBackend.hpp>
#include <kern/thread_call.h>
#include <sys/vnode.h>

namespace
{

    static_assert(sizeof(memory_object_t) == sizeof(void*));
    using PageValidator  = void (*)(vnode_t, memory_object_t, memory_object_offset_t, const void*, int*, int*, int*);
    using RangeValidator = boolean_t (*)(vnode_t, memory_object_t, memory_object_offset_t, const void*, vm_size_t,
                                         unsigned*);
    // Darwin 23–25 的 vnop_verify_args 共用此前缀；25 的尾部 verifykind 保留在调用者参数中。
    struct FileVerifyArguments
    {
        void*         descriptor;
        vnode_t       vnode;
        off_t         offset;
        const void*   data;
        size_t        size;
        size_t*       blockSize;
        void**        context;
        UInt32        flags;
        vfs_context_t vfsContext;
    };
    static_assert(sizeof(FileVerifyArguments) == 72);
    static_assert(offsetof(FileVerifyArguments, data) == 24);
    static_assert(offsetof(FileVerifyArguments, vfsContext) == 64);
    using FilesystemVerifier = int (*)(void*);

    const char*                   apfsPath = "/System/Library/Extensions/apfs.kext/Contents/MacOS/apfs";
    KernelPatcher::KextInfo       apfs{"com.apple.filesystems.apfs",     &apfsPath, 1, {true}, {},
                                 KernelPatcher::KextInfo::Unloaded};
    mach_vm_address_t             originalPage{0}, originalRange{0}, originalVerify{0};
    const UserOpenGLData::Device* device{nullptr};
    const VPF::DisplayManifest*   manifests[2]{nullptr, nullptr};
    thread_call_t                 reportCall{nullptr};
    UInt32                        appliedMask{0}, dynamicMask{0}, errorMask{0}, planMask{0}, evidenceMask{0};
    UInt32                        groupStates[2]{}, verifyMask{0};
    constexpr UInt32              PageSize = UserOpenGLResolver::PageSize;

    // 仅在命中或写入失败后排队发布诊断；初始化不启动工作线程。
    __attribute__((noinline)) void publish(thread_call_param_t, thread_call_param_t)
    {
        const auto applied = __atomic_load_n(&appliedMask, __ATOMIC_RELAXED);
        const auto dynamic = __atomic_load_n(&dynamicMask, __ATOMIC_RELAXED);
        const auto errors  = __atomic_load_n(&errorMask, __ATOMIC_RELAXED);
        const auto plans   = __atomic_load_n(&planMask, __ATOMIC_RELAXED);
        const auto proofs  = __atomic_load_n(&evidenceMask, __ATOMIC_RELAXED);
        const auto checks  = __atomic_load_n(&verifyMask, __ATOMIC_RELAXED);
        auto&      nred    = NRed::singleton();
        nred.setProp32("NRedOpenGLVerifyObserved", (checks & 1) != 0);
        nred.setProp32("NRedOpenGLVerifyRestored", (checks & 2) != 0);
        nred.setProp32("NRedOpenGLVerifyRejected", (checks & 4) != 0);
        nred.setProp32("NRedOpenGLVerifyAllocationFailed", (checks & 8) != 0);
        nred.setProp32("NRedOpenGLSEApplied", (applied & 1) != 0);
        nred.setProp32("NRedOpenGLDualQuadApplied", (applied & 2) != 0);
        nred.setProp32("NRedOpenGLDynamicApplied", dynamic == 3);
        nred.setProp32("NRedOpenGLWriteRejected", (errors & 1) != 0);
        nred.setProp32("NRedOpenGLRestoreFailed", (errors & 2) != 0);
        nred.setProp32("NRedOpenGLVPFPlanVerified", plans == 3);
        nred.setProp32("NRedOpenGLVPFEvidenceMask", proofs);
        nred.setProp32("NRedOpenGLHwInfoGroupState", __atomic_load_n(&groupStates[0], __ATOMIC_RELAXED));
        nred.setProp32("NRedOpenGLDualQuadGroupState", __atomic_load_n(&groupStates[1], __ATOMIC_RELAXED));
    }

    void report(UInt32& mask, UInt32 bits)
    {
        if ((__atomic_fetch_or(&mask, bits, __ATOMIC_RELAXED) & bits) != bits) { thread_call_enter(reportCall); }
    }

    bool restoreWriting()
    {
        if (MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock) == KERN_SUCCESS) { return true; }
        // Lilu 失败时也会释放锁；恢复重试必须重新配对加锁，不能重复解锁。
        if (MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) != KERN_SUCCESS) { return false; }
        return MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock) == KERN_SUCCESS;
    }

    __attribute__((noinline)) void deliverBlock(const VPF::ValidatedPageWorkspace& workspace, const UInt8* bytes,
                                                const UInt32 kind)
    {
        const auto             group = workspace.plan.manifest()->group;
        VPF::DisplayRuntime<1> runtime;
        runtime.reset(reinterpret_cast<UInt64>(bytes), device->major, device->deviceID,
                      UserPatchCoordinator::requestedDisplayGroups() & VPF::displayGroupBit(group));
        const auto feature = kind == 0 ? VPF::FeatureId::DisplayOpenGLHwInfo : VPF::FeatureId::DisplayOpenGLDualQuad;
        if (!runtime.verify(workspace.evidenceVerified ? VPF::featureBit(feature) : 0, VPF::ExternalOpenGLPageHook)
            || !runtime.begin())
        {
            return;
        }
        __atomic_fetch_or(&planMask, 1U << kind, __ATOMIC_RELAXED);
        if (workspace.evidenceVerified) { __atomic_fetch_or(&evidenceMask, 1U << kind, __ATOMIC_RELAXED); }
        const auto outcome = VPF::applyValidatedPage(
            workspace, const_cast<UInt8*>(bytes),
            [&]
            {
                return runtime.ready()
                       && MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) == KERN_SUCCESS;
            },
            restoreWriting);
        switch (outcome) {
            case VPF::PagePatchOutcome::Delivered:
            case VPF::PagePatchOutcome::AlreadyInstalled: {
                runtime.installed(VPF::featureBit(feature));
                if (workspace.plan.dynamic()) { report(dynamicMask, 1U << kind); }
                report(appliedMask, 1U << kind);
            } break;
            case VPF::PagePatchOutcome::WriteFailed: {
                runtime.failed(true);
                report(errorMask, 1);
            } break;
            case VPF::PagePatchOutcome::RestoreFailed: {
                runtime.failed(false);
                report(errorMask, 2);
            } break;
            default: runtime.failed(true); break;
        }
        const auto state = static_cast<UInt32>(runtime.state(group));
        if (__atomic_exchange_n(&groupStates[kind], state, __ATOMIC_RELAXED) != state) {
            thread_call_enter(reportCall);
        }
    }
    __attribute__((noinline)) void patchBlock(const VPF::OpenGLPageContext& context, const UInt8* bytes, UInt64 offset,
                                              UInt32 kind)
    {
        if (kind >= 2 || manifests[kind] == nullptr) { return; }
        VPF::ValidatedPageWorkspace workspace;
        if (!VPF::inspectValidatedPage(*manifests[kind], *device, kind, bytes, offset, context, workspace)) { return; }
        deliverBlock(workspace, bytes, kind);
    }

    bool isDriver(vnode_t vnode)
    {
        const auto* name = vnode_getname(vnode);
        if (!name) { return false; }
        const bool driver = strcmp(name, "AMDRadeonX5000GLDriver") == 0;
        vnode_putname(name);
        return driver;
    }

    // 解压页入会复用已缓存的执行页；校验副本只还原完整匹配的本设备补丁，不改写共享页。
    template<typename Verify>
    __attribute__((noinline)) int verifyFileData(vnode_t vnode, off_t offset, const void* data, size_t size,
                                                 UInt32 flags, Verify&& verify)
    {
        if (!device || !reportCall || __atomic_load_n(&appliedMask, __ATOMIC_RELAXED) == 0 || !vnode || !data
            || (flags != 0 && flags != 2) || size < PageSize || (static_cast<UInt64>(offset) & (PageSize - 1)) != 0
            || static_cast<UInt64>(offset) > UserOpenGLResolver::MaxFileSize - PageSize
            || size > UserOpenGLResolver::MaxFileSize - static_cast<UInt64>(offset) || !isDriver(vnode))
        {
            return verify(data);
        }
        report(verifyMask, 1);
        const auto* bytes = static_cast<const UInt8*>(data);
        UInt8*      copy{nullptr};
        for (size_t position = 0; PageSize <= size - position; position += PageSize) {
            for (UInt32 kind = 0; kind < 2; ++kind) {
                if ((__atomic_load_n(&appliedMask, __ATOMIC_RELAXED) & (1U << kind)) == 0) { continue; }
                UserOpenGLResolver::Resolved resolved;
                const auto                   fileOffset = static_cast<UInt64>(offset) + position;
                if (!UserOpenGLResolver::resolvePage(*device, bytes + position, fileOffset, kind, resolved)
                    || !resolved.alreadyPatched)
                {
                    continue;
                }
                const auto& function = device->resolver->functions[kind];
                const auto  start    = device->resolver->locations[kind].offset;
                memcpy(resolved.patched, device->patches[kind].original, resolved.size);
                for (UInt32 r = 0; r < function.originalCount; ++r) {
                    const auto& ref = function.originalRefs[r];
                    if (ref.indirect || ref.offset < start || ref.offset - start > resolved.size - 4
                        || !UserSharedCache::setRelative(resolved.patched, ref.offset - start,
                                                         fileOffset + resolved.offset, ref.rva))
                    {
                        if (copy) { kern_os_free(copy); }
                        return verify(data);
                    }
                }
                if (!copy) {
                    copy = static_cast<UInt8*>(kern_os_malloc(size));
                    if (!copy) {
                        report(verifyMask, 8);
                        return verify(data);
                    }
                    memcpy(copy, bytes, size);
                }
                memcpy(copy + position + resolved.offset, resolved.patched, resolved.size);
            }
        }
        if (copy) { report(verifyMask, 2); }
        const auto result = verify(copy ? copy : data);
        if (result != 0) { report(verifyMask, 4); }
        if (copy) { kern_os_free(copy); }
        return result;
    }

    // 挂接实际文件系统操作，覆盖内核中被内联的 VNOP_VERIFY 分派。
    __attribute__((noinline)) int verifyFilesystem(void* raw)
    {
        FileVerifyArguments arguments;
        memcpy(&arguments, raw, sizeof(arguments));
        return verifyFileData(arguments.vnode, arguments.offset, arguments.data, arguments.size, arguments.flags,
                              [&](const void* bytes)
                              {
                                  auto* buffer = static_cast<UInt8*>(raw) + offsetof(FileVerifyArguments, data);
                                  memcpy(buffer, &bytes, sizeof(bytes));
                                  const auto result = reinterpret_cast<FilesystemVerifier>(originalVerify)(raw);
                                  memcpy(buffer, &arguments.data, sizeof(arguments.data));
                                  return result;
                              });
    }

    // 超出驱动文件上限的页不取名；范围验证整段只取一次名称。
    __attribute__((noinline)) void patchPage(vnode_t vnode, const UInt8* data, UInt64 offset, vm_size_t size,
                                             UInt32 validated, UInt32 tainted, UInt32 nx)
    {
        if (!device || !reportCall || !vnode || !data || size < PageSize
            || offset > UserOpenGLResolver::MaxFileSize - PageSize || !isDriver(vnode))
        {
            return;
        }
        for (vm_size_t position = 0; PageSize <= size - position; position += PageSize) {
            const VPF::OpenGLPageContext context{validated, tainted, nx, offset + position, true};
            for (UInt32 kind = 0; kind < 2; ++kind) { patchBlock(context, data + position, offset + position, kind); }
        }
    }

    __attribute__((noinline)) void validatePage(vnode_t vnode, memory_object_t pager, memory_object_offset_t offset,
                                                const void* data, int* validated, int* tainted, int* nx)
    {
        reinterpret_cast<PageValidator>(originalPage)(vnode, pager, offset, data, validated, tainted, nx);
        // x86 的 4 KiB 整页验证可返回四位全真掩码，旧布尔返回仍为 1。
        if (validated && (*validated == 1 || *validated == 0xF) && tainted && *tainted == 0 && nx && *nx == 0
            && (offset & (PageSize - 1)) == 0)
        {
            patchPage(vnode, static_cast<const UInt8*>(data), offset, PageSize, static_cast<UInt32>(*validated),
                      static_cast<UInt32>(*tainted), static_cast<UInt32>(*nx));
        }
    }

    __attribute__((noinline)) boolean_t validateRange(vnode_t vnode, memory_object_t pager,
                                                      memory_object_offset_t offset, const void* data, vm_size_t size,
                                                      unsigned* tainted)
    {
        const auto result = reinterpret_cast<RangeValidator>(originalRange)(vnode, pager, offset, data, size, tainted);
        if (!result || !tainted || *tainted != 0 || !data || (offset & (PageSize - 1)) != 0
            || offset > UserOpenGLResolver::MaxFileSize || size > UserOpenGLResolver::MaxFileSize - offset)
        {
            return result;
        }
        patchPage(vnode, static_cast<const UInt8*>(data), offset, size, 1U, static_cast<UInt32>(*tainted), 0U);
        return result;
    }

}    // namespace

void UserOpenGL::registerKext()
{
    const auto major = currentKernelVersion().major();
    if (major >= 23 && major <= 25 && checkKernelArgument("-NRedDisplayFix")) { lilu.onKextLoadForce(&apfs); }
}

void UserOpenGL::init(KernelPatcher&)
{
    if ((UserPatchCoordinator::requestedDisplayGroups() & VPF::OpenGLDisplayGroups) == 0) { return; }
    manifests[0] = manifests[1] = nullptr;
    device = UserOpenGLData::findDevice(currentKernelVersion().major(), NRed::singleton().getDeviceID());
    if (!device) { return; }
    manifests[0] = VPF::DisplayManifestData::findOpenGL(device->major, device->deviceID, 0);
    manifests[1] = VPF::DisplayManifestData::findOpenGL(device->major, device->deviceID, 1);
    reportCall =
        thread_call_allocate_with_options(publish, nullptr, THREAD_CALL_PRIORITY_KERNEL, THREAD_CALL_OPTIONS_ONCE);
    if (!reportCall) {
        NRed::singleton().setProp32("NRedOpenGLRejected", 1);
        return;
    }
}

void UserOpenGL::processKext(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
{
    if (!device || !reportCall || id != apfs.loadIndex || originalVerify != 0) { return; }
    KernelPatcher::RouteRequest verifyRequest{"_apfs_vnop_verify", verifyFilesystem, originalVerify};
    if (!patcher.routeMultipleLong(id, &verifyRequest, 1, slide, size)) {
        patcher.clearError();
        thread_call_free(reportCall);
        reportCall = nullptr;
        NRed::singleton().setProp32("NRedOpenGLRejected", 3);
        return;
    }
    UInt32                      backends{0};
    KernelPatcher::RouteRequest pageRequest{"_cs_validate_page", validatePage, originalPage};
    if (patcher.routeMultipleLong(KernelPatcher::KernelID, &pageRequest, 1)) { backends |= 1; }
    patcher.clearError();
    KernelPatcher::RouteRequest rangeRequest{"_cs_validate_range", validateRange, originalRange};
    if (patcher.routeMultipleLong(KernelPatcher::KernelID, &rangeRequest, 1)) { backends |= 2; }
    patcher.clearError();
    if (!backends) {
        thread_call_free(reportCall);
        reportCall = nullptr;
        NRed::singleton().setProp32("NRedOpenGLRejected", 2);
        return;
    }
    NRed::singleton().setProp32("NRedOpenGLRevision", 7);
    NRed::singleton().setProp32("NRedOpenGLVerifyEnabled", 1);
    NRed::singleton().setProp32("NRedOpenGLPageLocal", 1);
    NRed::singleton().setProp32("NRedOpenGLEnabled", 1);
    NRed::singleton().setProp32("NRedOpenGLBackends", backends);
    NRed::singleton().setProp32("NRedOpenGLVPFRevision", 1);
    NRed::singleton().setProp32("NRedOpenGLVPFManifestMask",
                                (manifests[0] != nullptr ? 1U : 0U) | (manifests[1] != nullptr ? 2U : 0U));
}
