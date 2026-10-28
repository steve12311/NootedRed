// VPF：验证后的只读计划；只引用当前地址空间中的已核验字节，不执行写入。
#pragma once
#include <VPF/PatchManifest.hpp>

namespace VPF
{

    constexpr UInt32 MaxPlanImages   = 16;
    constexpr UInt32 MaxPlanSites    = 32;
    constexpr UInt32 MaxPlanPatchSize = 512;

    // 与安装器的 originals/patched 判定保持一致；Mixed/Unknown 不得进入安装。
    enum class PlanState : UInt8
    {
        Unknown,
        AllOriginal,
        AllPatched,
        Mixed,
    };

    struct PlanImage
    {
        const char*               path{nullptr};
        UInt64                    address{0};
        UserSharedCache::Range    text{};
        UserSharedCache::Range    segments[8]{};
        UInt32                    segmentCount{0};

        bool contains(UInt64 value, UInt64 size) const
        {
            for (UInt32 i = 0; i < segmentCount; ++i) {
                if (segments[i].contains(value, size)) { return true; }
            }
            return false;
        }
    };

    struct PlanSite
    {
        UInt64       address{0};    // 当前地址空间绝对地址
        UInt64       offset{0};     // 相对缓存基址
        UInt32       size{0};
        UInt32       functionIndex{NoFunction};
        UInt32       instructionOffset{0};
        const UInt8* original{nullptr};
        const UInt8* replacement{nullptr};
    };

    class VerifiedPatchPlan
    {
        const FeatureManifest* manifest_{nullptr};
        UInt64                 base_{0};
        UserSharedCache::Range cache_{};
        UInt32                 imageCount_{0}, siteCount_{0}, reads_{0};
        PlanImage              images_[MaxPlanImages]{};
        PlanSite               sites_[MaxPlanSites]{};
        PlanState              state_{PlanState::Unknown};
        bool                   valid_{false};

    public:
        void reset(const FeatureManifest& manifest, UInt64 base, const UserSharedCache::Range& cache)
        {
            manifest_   = &manifest;
            base_       = base;
            cache_      = cache;
            imageCount_ = siteCount_ = reads_ = 0;
            state_      = PlanState::Unknown;
            valid_      = false;
        }

        // 显示组可把实际解析出的补丁先装入计划，再按读回结果 seal；
        // 此时不绑定视频 FeatureManifest，站点也不得携带函数索引。
        void reset(const UInt64 base, const UserSharedCache::Range& cache)
        {
            manifest_   = nullptr;
            base_       = base;
            cache_      = cache;
            imageCount_ = siteCount_ = reads_ = 0;
            state_      = PlanState::Unknown;
            valid_      = false;
        }

        bool addImage(const char* path, UInt64 address, const UserSharedCache::Range& text,
                      const UserSharedCache::Range* segments, UInt32 segmentCount)
        {
            if (imageCount_ == MaxPlanImages || !path || address == 0 || text.size == 0 || segmentCount > 8
                || (segmentCount != 0 && segments == nullptr) || address >= UserSharedCache::UserLimit
                || text.address >= UserSharedCache::UserLimit || text.size > UserSharedCache::UserLimit - text.address
                || (cache_.size != 0
                    && (!cache_.contains(address, 1) || !cache_.contains(text.address, text.size))))
            {
                return false;
            }
            auto& image = images_[imageCount_++];
            image.path         = path;
            image.address      = address;
            image.text         = text;
            image.segmentCount = segmentCount;
            for (UInt32 i = 0; i < segmentCount; ++i) { image.segments[i] = segments[i]; }
            return true;
        }

        bool addSite(UInt64 address, UInt64 offset, UInt32 size, UInt32 functionIndex, UInt32 instructionOffset,
                     const UInt8* original, const UInt8* replacement)
        {
            if (siteCount_ == MaxPlanSites || size == 0 || size > MaxPlanPatchSize || !original || !replacement
                || address < base_ || address - base_ != offset
                || address >= UserSharedCache::UserLimit || size > UserSharedCache::UserLimit - address
                || (functionIndex != NoFunction
                    && (!manifest_ || manifest_->resolver.functionCount == 0
                        || functionIndex >= manifest_->resolver.functionCount)))
            {
                return false;
            }
            for (UInt32 i = 0; i < siteCount_; ++i) {
                const auto& other = sites_[i];
                if (UserSharedCache::Range{address, size}.contains(other.address, other.size)
                    || UserSharedCache::Range{other.address, other.size}.contains(address, size))
                {
                    return false;
                }
            }
            sites_[siteCount_++] = {address, offset, size, functionIndex, instructionOffset, original, replacement};
            return true;
        }

        void setReads(UInt32 reads) { reads_ = reads; }

        bool seal(PlanState state)
        {
            valid_ = base_ != 0 && cache_.size != 0 && siteCount_ != 0;
            for (UInt32 i = 0; valid_ && i < siteCount_; ++i) {
                if (!cache_.contains(sites_[i].address, sites_[i].size)) { valid_ = false; }
            }
            state_ = state;
            return valid_;
        }

        bool               valid() const { return valid_; }
        bool               installable() const
        {
            return valid_ && (state_ == PlanState::AllOriginal || state_ == PlanState::AllPatched);
        }
        const FeatureManifest* manifest() const { return manifest_; }
        FeatureId          feature() const { return manifest_ ? manifest_->feature : FeatureId::Count; }
        EvidenceKind       evidence() const { return manifest_ ? manifest_->evidence : EvidenceKind::ExactValidatedBuild; }
        PlanState          state() const { return state_; }
        UInt64             base() const { return base_; }
        UInt32             reads() const { return reads_; }
        UInt32             imageCount() const { return imageCount_; }
        UInt32             siteCount() const { return siteCount_; }
        const PlanImage&   image(UInt32 index) const { return images_[index]; }
        const PlanSite&    site(UInt32 index) const { return sites_[index]; }
    };

}    // namespace VPF
