// VPF：共享缓存页面事务的统一执行器；只编排已核验计划，不解析、不选择功能。
#pragma once
#include <IOKit/IOTypes.h>
#include <stddef.h>

namespace VPF
{

    enum class TransactionStatus : UInt8
    {
        Installed,
        CheckFailed,
        BeforeWriteFailed,
        ProtectionFailed,
        WriteFailed,
        ReadbackFailed,
        RestoreFailed,
    };

    // Ops 由调用方提供：check/makeWritable/prepareRollback/restore/write。
    // 执行器不直接依赖 vm_map/mach API，便于内核与用户态夹具共用同一实现。
    template<typename Ops, typename Patch, typename Page, UInt32 MaxPatchSize>
    class SharedRegionTransaction
    {
        Ops&      ops_;
        UInt64    base_;
        const Patch* patches_;
        UInt32       count_;
        UInt8 (*saved_)[MaxPatchSize];
        Page*    pages_;
        UInt32   pageCount_;
        UInt32   protectedPrefix_;

        bool rollback(const UInt32 touched)
        {
            bool ok = true;
            for (UInt32 remaining = touched; remaining > 0; --remaining) {
                const UInt32 index = remaining - 1;
                // 守卫前缀回滚失败后保留，避免把仍在使用的安全入口撤掉。
                if (!ok && index < protectedPrefix_) { continue; }
                const UInt64 address = base_ + patches_[index].offset;
                if (!ops_.prepareRollback(pages_, pageCount_, address, patches_[index].size)) {
                    ok = false;
                    continue;
                }
                if (ops_.write(saved_[index], address, patches_[index].size) != 0) { ok = false; }
            }
            if (!ops_.restore(pages_, pageCount_)) { ok = false; }
            return ok;
        }

    public:
        struct Result
        {
            TransactionStatus status{TransactionStatus::CheckFailed};
            UInt32            touched{0};
            bool              rollbackOk{false};
            bool              restoreOk{false};
        };

        SharedRegionTransaction(Ops& ops, const UInt64 base, const Patch* patches, const UInt32 count,
                                UInt8 (*saved)[MaxPatchSize], Page* pages, const UInt32 pageCount,
                                const UInt32 protectedPrefix)
            : ops_(ops), base_(base), patches_(patches), count_(count), saved_(saved), pages_(pages),
              pageCount_(pageCount), protectedPrefix_(protectedPrefix)
        {
        }

        // 顺序：权限检查 -> 写前回调（如创建守卫页）-> COW 可写 -> 写 -> 分块读回
        // -> 恢复 RX -> 必要时反向回滚。失败时返回真正修改过的前缀，供调用方保留守卫页。
        template<typename BeforeWrite>
        Result commit(BeforeWrite beforeWrite)
        {
            if (!patches_ || !saved_ || count_ == 0 || (pageCount_ != 0 && !pages_)) {
                return {TransactionStatus::CheckFailed, 0, false, false};
            }
            for (UInt32 i = 0; i < pageCount_; ++i) {
                if (!ops_.check(pages_[i])) { return {TransactionStatus::CheckFailed, 0, false, false}; }
            }
            if (!beforeWrite()) { return {TransactionStatus::BeforeWriteFailed, 0, false, false}; }
            for (UInt32 i = 0; i < pageCount_; ++i) {
                if (!ops_.makeWritable(pages_[i])) {
                    const bool restoreOk = ops_.restore(pages_, pageCount_);
                    return {TransactionStatus::ProtectionFailed, 0, false, restoreOk};
                }
            }
            for (UInt32 i = 0; i < count_; ++i) {
                if (ops_.write(patches_[i].patched, base_ + patches_[i].offset, patches_[i].size) != 0) {
                    const bool rollbackOk = rollback(i + 1);
                    return {TransactionStatus::WriteFailed, i + 1, rollbackOk, true};
                }
            }
            // 64 字节分块读回，避免为 448/452 字节补丁增加大栈帧。
            UInt8  chunk[64];
            for (UInt32 i = 0; i < count_; ++i) {
                const UInt8* expected = patches_[i].patched;
                for (UInt32 offset = 0; offset < patches_[i].size;) {
                    const UInt32 length = patches_[i].size - offset < sizeof(chunk) ? patches_[i].size - offset :
                                                                                     static_cast<UInt32>(sizeof(chunk));
                    if (ops_.read(base_ + patches_[i].offset + offset, chunk, length) != 0) {
                        const bool rollbackOk = rollback(count_);
                        return {TransactionStatus::ReadbackFailed, count_, rollbackOk, true};
                    }
                    for (UInt32 j = 0; j < length; ++j) {
                        if (chunk[j] != expected[offset + j]) {
                            const bool rollbackOk = rollback(count_);
                            return {TransactionStatus::ReadbackFailed, count_, rollbackOk, true};
                        }
                    }
                    offset += length;
                }
            }
            if (!ops_.restore(pages_, pageCount_)) {
                const bool rollbackOk = rollback(count_);
                return {TransactionStatus::RestoreFailed, count_, rollbackOk, false};
            }
            return {TransactionStatus::Installed, count_, true, true};
        }
    };

}    // namespace VPF
