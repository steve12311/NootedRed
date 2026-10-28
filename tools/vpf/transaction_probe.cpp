// VPF 执行器探针：写入/读回/保护/回滚顺序与守卫前缀保留语义。
#include <VPF/TransactionExecutor.hpp>

#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
    constexpr UInt32 MaxPatchSize = 64;
    constexpr UInt64 Base          = 0x100000;

    struct Page
    {
        UInt64 address{Base};
        int    protection{5};
        bool   writable{false};
    };

    struct Patch
    {
        UInt64       offset;
        const UInt8* original;
        const UInt8* patched;
        UInt32       size;
    };

    struct Ops
    {
        std::vector<UInt8> memory = std::vector<UInt8>(0x1000, 0xCC);
        Page               page{};
        unsigned           checks{0}, writable{0}, writes{0}, restores{0}, rollbacks{0};
        bool               failCheck{false}, failWritable{false}, failRead{false}, failRestore{false};
        unsigned           failWriteAt{0}, failRestoreAt{0}, failPrepareAt{0};
        UInt64             failPrepareAddress{0};

        bool check(Page& value)
        {
            ++checks;
            value.protection = 5;
            return !failCheck;
        }
        bool makeWritable(Page& value)
        {
            ++writable;
            value.writable = true;
            return !failWritable;
        }
        int write(const UInt8* bytes, const UInt64 address, const UInt32 size)
        {
            ++writes;
            if (failWriteAt != 0 && writes == failWriteAt) { return 1; }
            std::memcpy(memory.data() + (address - Base), bytes, size);
            return 0;
        }
        int read(const UInt64 address, UInt8* bytes, const UInt32 size)
        {
            if (failRead || address < Base || address - Base + size > memory.size()) { return 1; }
            std::memcpy(bytes, memory.data() + (address - Base), size);
            return 0;
        }
        bool prepareRollback(Page*, const UInt32, const UInt64 address, const UInt32)
        {
            ++rollbacks;
            if (failPrepareAt != 0 && rollbacks == failPrepareAt) { return false; }
            return failPrepareAddress == 0 || address != failPrepareAddress;
        }
        bool restore(Page*, const UInt32)
        {
            ++restores;
            if (failRestoreAt != 0 && restores == failRestoreAt) { return false; }
            return !failRestore;
        }
    };

    using Transaction = VPF::SharedRegionTransaction<Ops, Patch, Page, MaxPatchSize>;
}

int main()
{
    UInt8 original[2][32], patched[2][32], saved[2][MaxPatchSize];
    std::memset(original, 0x11, sizeof(original));
    std::memset(patched, 0x22, sizeof(patched));
    std::memset(saved, 0x11, sizeof(saved));
    const Patch patches[] = {{0x10, original[0], patched[0], sizeof(original[0])},
                             {0x200, original[1], patched[1], sizeof(original[1])}};

    auto run = [&](Ops& ops, const UInt32 protectedPrefix, bool beforeWriteResult = true) {
        std::memcpy(ops.memory.data() + 0x10, original[0], sizeof(original[0]));
        std::memcpy(ops.memory.data() + 0x200, original[1], sizeof(original[1]));
        Transaction transaction(ops, Base, patches, 2, saved, &ops.page, 1, protectedPrefix);
        return transaction.commit([&] { return beforeWriteResult; });
    };

    // 正常安装：检查、COW、两次写、读回、恢复 RX。
    {
        Ops ops;
        auto result = run(ops, 0);
        assert(result.status == VPF::TransactionStatus::Installed && result.rollbackOk && result.restoreOk);
        assert(ops.checks == 1 && ops.writable == 1 && ops.writes == 2 && ops.restores == 1);
        assert(std::memcmp(ops.memory.data() + 0x10, patched[0], sizeof(patched[0])) == 0);
        assert(std::memcmp(ops.memory.data() + 0x200, patched[1], sizeof(patched[1])) == 0);
    }
    // 权限检查失败：不写、不改保护。
    {
        Ops ops;
        ops.failCheck = true;
        auto result = run(ops, 0);
        assert(result.status == VPF::TransactionStatus::CheckFailed);
        assert(ops.writes == 0 && ops.writable == 0 && ops.restores == 0);
    }
    // 写前回调失败（守卫分配失败）：不写、不改保护。
    {
        Ops ops;
        auto result = run(ops, 0, false);
        assert(result.status == VPF::TransactionStatus::BeforeWriteFailed);
        assert(ops.writes == 0 && ops.writable == 0 && ops.restores == 0);
    }
    // COW 失败：恢复页面，不写。
    {
        Ops ops;
        ops.failWritable = true;
        auto result = run(ops, 0);
        assert(result.status == VPF::TransactionStatus::ProtectionFailed && result.restoreOk);
        assert(ops.writes == 0 && ops.restores == 1);
    }
    // 第二次写失败：反向回滚两次写。
    {
        Ops ops;
        ops.failWriteAt = 2;
        auto result = run(ops, 0);
        assert(result.status == VPF::TransactionStatus::WriteFailed && result.touched == 2 && result.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, original[0], sizeof(original[0])) == 0);
        assert(std::memcmp(ops.memory.data() + 0x200, original[1], sizeof(original[1])) == 0);
    }
    // 读回不一致：回滚全部补丁。
    {
        Ops ops;
        ops.failRead = true;
        auto result = run(ops, 0);
        assert(result.status == VPF::TransactionStatus::ReadbackFailed && result.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, original[0], sizeof(original[0])) == 0);
        assert(std::memcmp(ops.memory.data() + 0x200, original[1], sizeof(original[1])) == 0);
    }
    // 守卫前缀：第二项回滚失败后，不撤销第一项守卫补丁。
    {
        Ops ops;
        ops.failWriteAt     = 2;
        ops.failPrepareAt   = 1;    // 回滚第 2 项时失败
        auto result = run(ops, 1);
        assert(result.status == VPF::TransactionStatus::WriteFailed && result.touched == 2 && !result.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, patched[0], sizeof(patched[0])) == 0);
        assert(std::memcmp(ops.memory.data() + 0x200, original[1], sizeof(original[1])) == 0);
    }
    // RX 恢复失败：回滚；回滚中的恢复也失败时报告 rollbackOk=false。
    {
        Ops ops;
        ops.failRestore = true;
        auto result = run(ops, 0);
        assert(result.status == VPF::TransactionStatus::RestoreFailed && !result.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, original[0], sizeof(original[0])) == 0);
        assert(std::memcmp(ops.memory.data() + 0x200, original[1], sizeof(original[1])) == 0);
    }

    std::printf("PASS VPF transaction probe: check/before/COW/write/readback/restore/rollback\n");
    return 0;
}
