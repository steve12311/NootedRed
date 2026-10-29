// VPF：能力状态机与按地址空间账本；地址空间销毁前必须清理对应记录。
#pragma once
#include <VPF/PatchManifest.hpp>

namespace VPF
{

    // 每个 feature、每个地址空间独立推进；HardwareValidated 不属于运行时状态。
    enum class CapabilityState : UInt8
    {
        Disabled,
        Eligible,
        ImageResolved,
        Verified,
        Prepared,
        Installing,
        Installed,
        RuntimeObserved,
        CommitFailed,
        RollbackVerified,
        RollbackFailed,
        Rejected,
        Unsupported,
        Quarantined,
    };

    constexpr const char* stateName(const CapabilityState state)
    {
        switch (state) {
            case CapabilityState::Disabled: return "Disabled";
            case CapabilityState::Eligible: return "Eligible";
            case CapabilityState::ImageResolved: return "ImageResolved";
            case CapabilityState::Verified: return "Verified";
            case CapabilityState::Prepared: return "Prepared";
            case CapabilityState::Installing: return "Installing";
            case CapabilityState::Installed: return "Installed";
            case CapabilityState::RuntimeObserved: return "RuntimeObserved";
            case CapabilityState::CommitFailed: return "CommitFailed";
            case CapabilityState::RollbackVerified: return "RollbackVerified";
            case CapabilityState::RollbackFailed: return "RollbackFailed";
            case CapabilityState::Rejected: return "Rejected";
            case CapabilityState::Unsupported: return "Unsupported";
            case CapabilityState::Quarantined: return "Quarantined";
        }
        return "Unknown";
    }

    constexpr bool canTransition(const CapabilityState from, const CapabilityState to)
    {
        if (from == to) { return true; }
        switch (from) {
            case CapabilityState::Disabled:
                return to == CapabilityState::Eligible || to == CapabilityState::Rejected
                       || to == CapabilityState::Unsupported;
            case CapabilityState::Eligible:
                return to == CapabilityState::ImageResolved || to == CapabilityState::Disabled
                       || to == CapabilityState::Rejected || to == CapabilityState::Unsupported;
            case CapabilityState::ImageResolved:
                return to == CapabilityState::Verified || to == CapabilityState::Disabled
                       || to == CapabilityState::Rejected;
            case CapabilityState::Verified:
                return to == CapabilityState::Prepared || to == CapabilityState::Disabled
                       || to == CapabilityState::Rejected;
            case CapabilityState::Prepared:
                return to == CapabilityState::Installing || to == CapabilityState::Disabled
                       || to == CapabilityState::Rejected;
            case CapabilityState::Installing:
                return to == CapabilityState::Installed || to == CapabilityState::CommitFailed
                       || to == CapabilityState::RollbackFailed;
            case CapabilityState::Installed:
                return to == CapabilityState::RuntimeObserved || to == CapabilityState::Disabled;
            case CapabilityState::RuntimeObserved:
                return to == CapabilityState::Disabled;
            case CapabilityState::CommitFailed:
                return to == CapabilityState::RollbackVerified || to == CapabilityState::RollbackFailed;
            case CapabilityState::RollbackVerified: return to == CapabilityState::Disabled;
            case CapabilityState::RollbackFailed: return to == CapabilityState::Quarantined;
            case CapabilityState::Rejected: return to == CapabilityState::Disabled;
            case CapabilityState::Unsupported: return false;    // 本次启动不再重试
            case CapabilityState::Quarantined: return false;    // 不自动重试
        }
        return false;
    }

    // 发布证据与运行时状态分离：运行时不能把自己标记为已在硬件验收。
    enum class ReleaseEvidence : UInt8
    {
        Unknown,
        ValidatedOnHardware,
    };

    // 有界账本，由调用方串行化；活跃 map 的隔离状态不得因进程身份变化而重置。
    template<UInt32 MaxEntries>
    class CapabilityLedgerStorage
    {
    public:
        static constexpr UInt32 Capacity = MaxEntries;

    private:
        struct Entry
        {
            UInt64          map{0};
            FeatureId       feature{FeatureId::DisplaySRD};
            CapabilityState state{CapabilityState::Disabled};
        };

        Entry  entries_[Capacity]{};
        UInt32 count_{0};

        Entry* find(const UInt64 map, const FeatureId feature)
        {
            for (UInt32 i = 0; i < count_; ++i) {
                if (entries_[i].map == map && entries_[i].feature == feature) { return &entries_[i]; }
            }
            return nullptr;
        }

        const Entry* find(const UInt64 map, const FeatureId feature) const
        {
            for (UInt32 i = 0; i < count_; ++i) {
                if (entries_[i].map == map && entries_[i].feature == feature) { return &entries_[i]; }
            }
            return nullptr;
        }

    public:
        void clear() { count_ = 0; }

        // 新条目从 Disabled 开始；非法迁移和 Quarantined 自动重试均拒绝。
        bool set(const UInt64 map, const FeatureId feature, const CapabilityState state)
        {
            if (!map) { return false; }
            auto* entry = find(map, feature);
            if (!entry) {
                if (count_ == Capacity || !canTransition(CapabilityState::Disabled, state)) { return false; }
                entry          = &entries_[count_++];
                entry->map     = map;
                entry->feature = feature;
                entry->state   = CapabilityState::Disabled;
            }
            if (!canTransition(entry->state, state)) { return false; }
            entry->state = state;
            return true;
        }

        CapabilityState state(const UInt64 map, const FeatureId feature) const
        {
            const auto* entry = find(map, feature);
            return entry ? entry->state : CapabilityState::Disabled;
        }

        void removeMap(const UInt64 map)
        {
            UInt32 write = 0;
            for (UInt32 read = 0; read < count_; ++read) {
                if (entries_[read].map != map) { entries_[write++] = entries_[read]; }
            }
            count_ = write;
        }

        UInt32 mapCount(const FeatureId feature, const CapabilityState state) const
        {
            UInt32 result = 0;
            for (UInt32 i = 0; i < count_; ++i) {
                if (entries_[i].feature == feature && entries_[i].state == state) { ++result; }
            }
            return result;
        }

        UInt32 installedMaps(const FeatureId feature) const
        {
            return mapCount(feature, CapabilityState::Installed) + mapCount(feature, CapabilityState::RuntimeObserved);
        }

        // 全局发布只能表示“至少一个已记录实例成功”，不能代表所有进程。
        bool anyInstalled(const FeatureId feature) const { return installedMaps(feature) != 0; }

        bool quarantined(const FeatureId feature) const
        {
            return mapCount(feature, CapabilityState::Quarantined) != 0;
        }
    };

    using CapabilityLedger = CapabilityLedgerStorage<64>;

}    // namespace VPF
