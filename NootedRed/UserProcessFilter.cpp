// 内核侧进程过滤：在共享缓存补丁前读取当前进程可执行文件的加载命令，
// 只跳过 UserProcessFilter.hpp 白名单中的基础系统命令。任何失败都回到补丁。
#include <Headers/kern_patcher.hpp>
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <NRed.hpp>
#include <UserPatchCoordinator.hpp>
#include <UserProcessFilter.hpp>
#include <sys/param.h>
#include <sys/proc.h>
#include <sys/uio.h>
#include <sys/vnode.h>

namespace
{

    using GetExecutableVnode = vnode_t (*)(proc_t);

    GetExecutableVnode getExecutableVnode{nullptr};
    UInt32             checkedCount{0}, skippedCount{0}, failureCount{0};

    constexpr UInt32 BufferBytes = UserProcessFilter::HeaderBytes + UserProcessFilter::MaxCommandBytes;

    struct ReadContext
    {
        vnode_t       vnode;
        vfs_context_t context;
    };

    UInt32 readVnode(void* opaque, const UInt64 offset, void* out, const UInt32 size)
    {
        auto& state = *static_cast<ReadContext*>(opaque);
        if (size == 0 || size > 0x7FFFFFFFU || offset > 0x7FFFFFFFFFFFFFFFULL) { return 0; }
        int resid = 0;
        // IO_NOAUTH：只读取刚完成 exec 的映像本身；仅执行权限的二进制也能判定，结果不返回给用户态。
        const auto error = vn_rdwr(UIO_READ, state.vnode, static_cast<caddr_t>(out), static_cast<int>(size),
                                   static_cast<off_t>(offset), UIO_SYSSPACE, IO_NOAUTH,
                                   vfs_context_ucred(state.context), &resid, vfs_context_proc(state.context));
        if (error != 0 || resid < 0 || static_cast<UInt32>(resid) > size) { return 0; }
        return size - static_cast<UInt32>(resid);
    }

    // 计数只在前 16 次和之后每 256 次写入 IORegistry，避免每次 exec 都持有注册表锁。
    void publishCount(UInt32& counter, const char* key)
    {
        const auto value = __atomic_add_fetch(&counter, 1U, __ATOMIC_RELAXED);
        if (value <= 16 || (value & 0xFFU) == 0) { NRed::singleton().setProp32(key, value); }
    }

    bool shouldPatch(void* opaqueProcess)
    {
        auto* process = static_cast<proc_t>(opaqueProcess);
        if (process == nullptr || getExecutableVnode == nullptr) { return true; }
        auto* vnode = getExecutableVnode(process);
        if (vnode == nullptr) { return true; }
        char path[MAXPATHLEN];
        int  pathLen = sizeof(path);
        bool patch   = true;
        if (vn_getpath(vnode, path, &pathLen) == 0 && pathLen > 1 && pathLen <= static_cast<int>(sizeof(path))) {
            // vn_getpath 的长度包含结尾 NUL。
            const auto length = static_cast<UInt32>(pathLen - 1);
            if (UserProcessFilter::eligiblePath(path, length)) {
                auto* buffer = static_cast<UInt8*>(IOMalloc(BufferBytes));
                if (buffer != nullptr) {
                    auto* context = vfs_context_create(nullptr);
                    if (context != nullptr) {
                        ReadContext state{vnode, context};
                        const auto  verdict = UserProcessFilter::classify(
                            path, length, readVnode, &state, buffer, buffer + UserProcessFilter::HeaderBytes);
                        vfs_context_rele(context);
                        patch = verdict != UserProcessFilter::Verdict::Skip;
                        if (verdict == UserProcessFilter::Verdict::PatchFormat) {
                            publishCount(failureCount, "NRedProcessFilterFormat");
                        }
                    }
                    IOFree(buffer, BufferBytes);
                }
                publishCount(checkedCount, "NRedProcessFilterChecked");
            }
        }
        vnode_put(vnode);
        if (!patch) { publishCount(skippedCount, "NRedProcessFilterSkipped"); }
        return patch;
    }

}    // namespace

namespace UserProcessFilter
{

    void init(KernelPatcher& patcher)
    {
        UserPatchCoordinator::processFilter() = nullptr;
        NRed::singleton().setProp32("NRedProcessFilterRevision", 3);
        if (checkKernelArgument("-NRedPatchAllProcs")) {
            NRed::singleton().setProp32("NRedProcessFilterDisabled", 1);
            return;
        }
        getExecutableVnode = reinterpret_cast<GetExecutableVnode>(
            patcher.solveSymbol(KernelPatcher::KernelID, "_proc_getexecutablevnode"));
        patcher.clearError();
        if (getExecutableVnode == nullptr) {
            NRed::singleton().setProp32("NRedProcessFilterDisabled", 2);
            SYSLOG("UserProcessFilter", "proc_getexecutablevnode unavailable; patching every process");
            return;
        }
        UserPatchCoordinator::processFilter() = shouldPatch;
        NRed::singleton().setProp32("NRedProcessFilterEnabled", 1);
        SYSLOG("UserProcessFilter", "shared-cache patches skip allowlisted basic system tools");
    }

}    // namespace UserProcessFilter
