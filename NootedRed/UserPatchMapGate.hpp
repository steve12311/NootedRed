// 仅串行化同一地址空间的补丁事务；等待者不持有注册表锁。
#pragma once

namespace UserPatchMapGate
{
    template<unsigned Capacity>
    struct Registry
    {
        static_assert(Capacity > 0);
        void* keys[Capacity]{};
        unsigned active{0}, peak{0};
    };

    template<unsigned Capacity>
    class Lease
    {
        Registry<Capacity>& registry;
        IOLock*             lock;
        void*               key;
        unsigned            slot{Capacity};

    public:
        Lease(Registry<Capacity>& registry, IOLock* lock, void* key) : registry(registry), lock(lock), key(key)
        {
            if (!lock || !key) { return; }
            IOLockLock(lock);
            for (;;) {
                unsigned free = Capacity;
                bool busy = false;
                for (unsigned i = 0; i < Capacity; ++i) {
                    if (registry.keys[i] == key) { busy = true; break; }
                    if (!registry.keys[i] && free == Capacity) { free = i; }
                }
                if (!busy && free != Capacity) {
                    registry.keys[free] = key;
                    if (++registry.active > registry.peak) { registry.peak = registry.active; }
                    slot = free;
                    break;
                }
                IOLockSleep(lock, busy ? key : static_cast<void*>(&registry), THREAD_UNINT);
            }
            IOLockUnlock(lock);
        }

        ~Lease()
        {
            if (slot == Capacity) { return; }
            IOLockLock(lock);
            registry.keys[slot] = nullptr;
            --registry.active;
            IOLockWakeup(lock, key, false);
            IOLockWakeup(lock, &registry, false);
            IOLockUnlock(lock);
        }

        explicit operator bool() const { return slot != Capacity; }
        Lease(const Lease&) = delete;
        Lease& operator=(const Lease&) = delete;
    };
}
