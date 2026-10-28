// DVPF Metal 计划执行器探针：读回判定、跨页、写/读回/保护失败与回滚。
#include <VPF/MetalTransactionExecutor.hpp>

#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
    constexpr UInt32 MaxPatchSize = 64;
    constexpr UInt32 MaxPages     = 4;
    constexpr UInt64 Base         = 0x100000;

    struct Page
    {
        UInt64 address{0};
        int    protection{0};
        bool   writable{false};
    };

    struct Ops
    {
        std::vector<UInt8> memory = std::vector<UInt8>(0x6000, 0xCC);
        Page               pages[MaxPages]{};
        unsigned           reads{0}, checks{0}, writable{0}, writes{0}, restores{0}, rollbacks{0};
        bool               failCheck{false}, failWritable{false}, failRestore{false};
        unsigned           failReadAt{0}, failWriteAt{0}, failRollbackAt{0};

        bool check(Page& page)
        {
            ++checks;
            page.protection = 5;
            return !failCheck;
        }
        bool makeWritable(Page& page)
        {
            ++writable;
            page.writable = true;
            return !failWritable;
        }
        bool prepareRollback(Page*, UInt32, UInt64, UInt32)
        {
            ++rollbacks;
            return failRollbackAt == 0 || rollbacks != failRollbackAt;
        }
        bool restore(Page*, UInt32)
        {
            ++restores;
            return !failRestore;
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
            ++reads;
            if ((failReadAt != 0 && reads == failReadAt) || address < Base || address - Base + size > memory.size()) {
                return 1;
            }
            std::memcpy(bytes, memory.data() + (address - Base), size);
            return 0;
        }
        template<size_t Capacity>
        bool collectPages(Page (&target)[Capacity], UInt32& count, const UInt64 address, const UInt32 size)
        {
            const auto last = (address + size - 1) & ~static_cast<UInt64>(4095);
            for (auto page = address & ~static_cast<UInt64>(4095); page <= last; page += 4096) {
                bool found = false;
                for (UInt32 i = 0; i < count; ++i) { found |= target[i].address == page; }
                if (found) { continue; }
                if (count == Capacity) { return false; }
                target[count++].address = page;
            }
            return true;
        }
    };

    using Transaction = VPF::MetalTransaction<Ops, Page, MaxPatchSize, MaxPages>;

    UInt8 original[3][32], patched[3][32];

    void resetMemory(Ops& ops)
    {
        std::memset(original, 0x11, sizeof(original));
        std::memset(patched, 0x22, sizeof(patched));
        std::memcpy(ops.memory.data() + 0x10, original[0], 4);
        std::memcpy(ops.memory.data() + 0x20, original[1], 4);
        std::memcpy(ops.memory.data() + 0x1010, original[2], 4);
    }

    VPF::VerifiedPatchPlan makePlan(const bool sealed = false)
    {
        VPF::VerifiedPatchPlan plan;
        plan.reset(Base, UserSharedCache::Range{Base, 0x4000});
        assert(plan.addSite(Base + 0x10, 0x10, 4, VPF::NoFunction, 0, original[0], patched[0]));
        assert(plan.addSite(Base + 0x20, 0x20, 4, VPF::NoFunction, 0, original[1], patched[1]));
        assert(plan.addSite(Base + 0x1010, 0x1010, 4, VPF::NoFunction, 0, original[2], patched[2]));
        if (sealed) { assert(plan.seal(VPF::PlanState::AllOriginal)); }
        return plan;
    }
}    // namespace

int main()
{
    // 读回后 AllOriginal，跨页收集并完整写入/读回/恢复。
    {
        Ops ops;
        resetMemory(ops);
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        assert(prepared.state == VPF::MetalPrepareState::AllOriginal && prepared.pageCount == 2
               && prepared.originals == 3 && prepared.patched == 0 && plan.valid());
        const auto committed = transaction.commit([] { return true; });
        assert(committed.status == VPF::TransactionStatus::Installed && committed.rollbackOk
               && committed.restoreOk);
        assert(ops.writes == 3 && ops.restores == 1 && ops.checks == 2);
        assert(std::memcmp(ops.memory.data() + 0x10, patched[0], 4) == 0);
        assert(std::memcmp(ops.memory.data() + 0x1010, patched[2], 4) == 0);
    }
    // 已完整安装的计划只报告 AllPatched，不进入写阶段。
    {
        Ops ops;
        resetMemory(ops);
        std::memcpy(ops.memory.data() + 0x10, patched[0], 4);
        std::memcpy(ops.memory.data() + 0x20, patched[1], 4);
        std::memcpy(ops.memory.data() + 0x1010, patched[2], 4);
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        assert(prepared.state == VPF::MetalPrepareState::AllPatched && ops.writes == 0);
        assert(transaction.commit([] { return true; }).status == VPF::TransactionStatus::CheckFailed);
        assert(ops.writes == 0);
    }
    // Mixed 状态拒绝，不写、不改权限。
    {
        Ops ops;
        resetMemory(ops);
        std::memcpy(ops.memory.data() + 0x10, patched[0], 4);
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        assert(prepared.state == VPF::MetalPrepareState::Mixed && prepared.fault == VPF::MetalPrepareFault::None);
        assert(ops.writes == 0 && ops.writable == 0);
    }
    // 读失败、页面超限、非法计划分别拒绝。
    {
        Ops ops;
        resetMemory(ops);
        ops.failReadAt = 2;
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(!transaction.prepare(prepared) && prepared.fault == VPF::MetalPrepareFault::ReadFailed);
    }
    {
        Ops ops;
        resetMemory(ops);
        for (UInt32 i = 0; i < 5; ++i) {
            std::memcpy(ops.memory.data() + 0x1000ULL * i, original[0], 4);
        }
        VPF::VerifiedPatchPlan plan;
        plan.reset(Base, UserSharedCache::Range{Base, 0x5000});
        // 五个站点跨五页，超过四个页面预算。
        for (UInt32 i = 0; i < 5; ++i) {
            assert(plan.addSite(Base + 0x1000ULL * i, 0x1000ULL * i, 4, VPF::NoFunction, 0, original[0],
                                patched[0]));
        }
        UInt8 saved[5][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(!transaction.prepare(prepared) && prepared.fault == VPF::MetalPrepareFault::PageLimit);
    }
    {
        Ops ops;
        VPF::VerifiedPatchPlan plan;
        plan.reset(Base, UserSharedCache::Range{Base, 0x10});
        assert(plan.addSite(Base + 0x20, 0x20, 4, VPF::NoFunction, 0, original[0], patched[0]));
        assert(!plan.seal(VPF::PlanState::AllOriginal));
    }
    // 写入/读回/权限恢复失败均反向回滚；任何失败后不发布 AllOriginal 之外的状态。
    for (const unsigned failure : {1U, 2U}) {
        Ops ops;
        resetMemory(ops);
        ops.failWriteAt = failure;
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        const auto committed = transaction.commit([] { return true; });
        assert(committed.status == VPF::TransactionStatus::WriteFailed && committed.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, original[0], 4) == 0);
        assert(std::memcmp(ops.memory.data() + 0x1010, original[2], 4) == 0);
    }
    {
        Ops ops;
        resetMemory(ops);
        ops.failReadAt = 4;    // 三次 prepare 读 + 第一次读回
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        const auto committed = transaction.commit([] { return true; });
        assert(committed.status == VPF::TransactionStatus::ReadbackFailed && committed.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, original[0], 4) == 0);
    }
    {
        Ops ops;
        resetMemory(ops);
        ops.failWritable = true;
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        const auto committed = transaction.commit([] { return true; });
        assert(committed.status == VPF::TransactionStatus::ProtectionFailed && ops.writes == 0);
    }
    {
        Ops ops;
        resetMemory(ops);
        ops.failRestore = true;
        auto plan = makePlan();
        UInt8 saved[3][MaxPatchSize]{};
        Page pages[MaxPages]{};
        Transaction transaction(ops, plan, Base, saved, pages);
        VPF::MetalPrepareResult prepared;
        assert(transaction.prepare(prepared));
        const auto committed = transaction.commit([] { return true; });
        assert(committed.status == VPF::TransactionStatus::RestoreFailed && !committed.rollbackOk);
        assert(std::memcmp(ops.memory.data() + 0x10, original[0], 4) == 0);
    }

    std::printf("PASS DVPF metal transaction probe: readback state, cross-page, mixed/missing evidence, write/readback/"
                "protection/restore rollback\n");
    return 0;
}
