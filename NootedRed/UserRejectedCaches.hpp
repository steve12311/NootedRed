// 仅记录完整镜像表中缺少目标镜像的缓存；进程读取或函数核验失败不进入拒绝表。
#pragma once
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/IOTypes.h>
#include <UserSharedCache.hpp>
#include <string.h>

namespace UserRejectedCaches
{

    constexpr UInt32 UUIDSize     = 16;
    constexpr UInt32 Slots        = 4;
    constexpr UInt32 Confirm      = 2;
    constexpr UInt32 RecheckEvery = 64;

    class Table
    {
        struct Entry
        {
            UInt8  uuid[UUIDSize]{};
            UInt32 rejects{0}, skips{0};
            bool   used{false};
        };

        IOLock* lock{nullptr};
        Entry   rejected[Slots]{};
        Entry   accepted[Slots]{};
        UInt32  nextRejected{0}, nextAccepted{0};

        static Entry* find(Entry* entries, const UInt8* uuid)
        {
            for (UInt32 i = 0; i < Slots; ++i) {
                if (entries[i].used && memcmp(entries[i].uuid, uuid, UUIDSize) == 0) { return &entries[i]; }
            }
            return nullptr;
        }

        static Entry& claim(Entry* entries, UInt32& next, const UInt8* uuid)
        {
            Entry* slot = nullptr;
            for (UInt32 i = 0; i < Slots && !slot; ++i) {
                if (!entries[i].used) { slot = &entries[i]; }
            }
            if (!slot) {
                slot = &entries[next];
                next = (next + 1) % Slots;
            }
            *slot = {};
            memcpy(slot->uuid, uuid, UUIDSize);
            slot->used = true;
            return *slot;
        }

    public:
        bool init()
        {
            if (!lock) { lock = IOLockAlloc(); }
            return lock != nullptr;
        }

        // true: la cache è stata rifiutata più volte e non è il turno della ricontrollo periodico.
        bool shouldSkip(const UInt8* uuid)
        {
            if (!lock || !uuid) { return false; }
            IOLockLock(lock);
            auto*      entry = find(rejected, uuid);
            const bool skip  = entry && entry->rejects >= Confirm && (++entry->skips % RecheckEvery) != 0;
            IOLockUnlock(lock);
            return skip;
        }

        void rejectMissingImages(const UInt8* uuid, const UserSharedCache::Input& input)
        {
            if (!lock || !uuid || input.failed || !input.tableRead || input.imageCount == 0 || input.imageCount > 16
                || !input.imageRefs)
            {
                return;
            }
            bool missing = false;
            for (UInt32 i = 0; i < input.imageCount; ++i) {
                if (input.imageAddresses[i] == 0) { missing = true; }
            }
            if (!missing) { return; }
            IOLockLock(lock);
            if (!find(accepted, uuid)) {
                auto* entry = find(rejected, uuid);
                if (!entry) { entry = &claim(rejected, nextRejected, uuid); }
                if (entry->rejects < 0xFFFFFFFFU) { ++entry->rejects; }
            }
            IOLockUnlock(lock);
        }

        void accept(const UInt8* uuid)
        {
            if (!lock || !uuid) { return; }
            IOLockLock(lock);
            if (auto* entry = find(rejected, uuid)) { *entry = {}; }
            if (!find(accepted, uuid)) { claim(accepted, nextAccepted, uuid); }
            IOLockUnlock(lock);
        }
    };

}    // namespace UserRejectedCaches
