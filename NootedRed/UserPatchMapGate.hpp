// 仅串行化同一地址空间的补丁事务；等待者不持有注册表锁。
#pragma once

namespace UserPatchMapGate
{

    template<unsigned Capacity>
    struct Registry
    {
        static_assert(Capacity > 0);
        void*    keys[Capacity]{};
        void*    owners[Capacity]{};
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
        Lease(Registry<Capacity>& registry, IOLock* lock, void* key, void* owner = current_thread()) :
            registry(registry),
            lock(lock),
            key(key)
        {
            if (!lock || !key || !owner) { return; }
            IOLockLock(lock);
            for (;;) {
                unsigned free = Capacity;
                bool     busy = false;
                // 同线程的嵌套事务不能等待外层释放，也不能跨 map 形成循环等待。
                for (unsigned i = 0; i < Capacity; ++i) {
                    if (registry.owners[i] == owner) {
                        IOLockUnlock(lock);
                        return;
                    }
                }
                for (unsigned i = 0; i < Capacity; ++i) {
                    if (registry.keys[i] == key) {
                        busy = true;
                        break;
                    }
                    if (!registry.keys[i] && free == Capacity) { free = i; }
                }
                if (!busy && free != Capacity) {
                    registry.keys[free]   = key;
                    registry.owners[free] = owner;
                    // peak 供锁外无锁读取诊断，写入仍在锁内。
                    if (++registry.active > registry.peak) {
                        __atomic_store_n(&registry.peak, registry.active, __ATOMIC_RELAXED);
                    }
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
            registry.keys[slot]   = nullptr;
            registry.owners[slot] = nullptr;
            --registry.active;
            IOLockWakeup(lock, key, false);
            IOLockWakeup(lock, &registry, false);
            IOLockUnlock(lock);
        }

        explicit operator bool() const { return slot != Capacity; }
        Lease(const Lease&)            = delete;
        Lease& operator=(const Lease&) = delete;
    };

}    // namespace UserPatchMapGate
