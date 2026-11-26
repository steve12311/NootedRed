// 复用原生 VCN2 引擎和客户端，修正已核实的跨驱动调用。
#include <Headers/kern_api.hpp>
#include <Headers/kern_iokit.hpp>
#include <Headers/kern_util.hpp>
#include <IOKit/pci/IOPCIDevice.h>
#include <Kexts.hpp>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <VCN.hpp>
#include <VCNCapabilities.hpp>
#include <VCNKernelData.hpp>
#include <VCNKernelProfiles.hpp>
#include <VP9Probe.hpp>
#include <libkern/OSKextLib.h>
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSNumber.h>

namespace
{

    static const char*      path = "/System/Library/Extensions/AMDRadeonX6000.kext/Contents/MacOS/AMDRadeonX6000";
    KernelPatcher::KextInfo x6000{"com.apple.kext.AMDRadeonX6000",  &path, 1, {true}, {},
                                  KernelPatcher::KextInfo::Unloaded};
    bool                    requested{false}, has5000{false}, has6000{false}, hooks{false}, engineAllocated{false};
    bool                    loadAttempted{false}, loadCompleted{false};
    mach_vm_address_t       acceleratorStartOriginal{0};
    mach_vm_address_t       safeCastOriginal{0}, newSharedOriginal{0}, newClientOriginal{0}, newVideoOriginal{0};
    mach_vm_address_t       newVideo6000{0};
    mach_vm_address_t       clientStart5000{0}, clientStop5000{0}, clientStart6000{0}, clientStop6000{0};
    mach_vm_address_t       copy5000{0}, copy6000{0}, sml5000{0}, sml6000{0}, initHardwareOriginal{0};
    mach_vm_address_t       signalOriginal{0};
    void                    (*notifyAccess)(void*){nullptr};
    OSMetaClass*            engineClass{nullptr};
    OSMetaClass*            sharedClass{nullptr};
    OSMetaClass*            clientClass{nullptr};
    struct Pair
    {
        const OSMetaClass* oldClass{nullptr};
        const OSMetaClass* newClass{nullptr};
    };
    Pair                  pairs[4];
    constexpr const char* classes[] = {"AMDGraphicsAccelerator", "AMDAccelVideoContext", "AMDAccelChannel",
                                       "IAMDHWChannel"};
    constexpr unsigned    lengths[] = {37, 35, 30, 28};
    UInt32                hardwareInitCount{0};
    bool                  vp9BackendReady{false};

    bool supported()
    {
        return requested && VCNCapabilities::rejection(NRed::singleton().getDeviceID(), currentKernelVersion()) == 0;
    }

    // 在加速器上发布已就绪的 VP9 解码能力。
    struct VP9ServiceSink
    {
        IOService* service;
        bool       string(const char* key, const char* value) { return service->setProperty(key, value); }
        bool       profiles(const char* key, const char* arrayKey, const UInt32* values, size_t count)
        {
            auto* array = OSArray::withCapacity(static_cast<unsigned>(count));
            auto* dict  = OSDictionary::withCapacity(1);
            bool  ok    = array && dict;
            for (size_t i = 0; ok && i < count; ++i) {
                auto* number = OSNumber::withNumber(values[i], 32);
                ok           = number && array->setObject(number);
                OSSafeReleaseNULL(number);
            }
            ok = ok && dict->setObject(arrayKey, array) && service->setProperty(key, dict);
            OSSafeReleaseNULL(array);
            OSSafeReleaseNULL(dict);
            return ok;
        }
        void remove(const char* key) { service->removeProperty(key); }
    };

    void publishVP9Probe(void* self, bool started)
    {
        VP9ServiceSink sink{static_cast<IOService*>(self)};
        bool           available;
        do {
            available         = __atomic_load_n(&vp9BackendReady, __ATOMIC_ACQUIRE);
            const auto status = VP9Probe::publish(sink, started && VCN::ready() && available);
            NRed::singleton().setProp32(VP9Probe::StatusKey, status);
        }
        while (available != __atomic_load_n(&vp9BackendReady, __ATOMIC_ACQUIRE));
    }

    bool acceleratorStart(void* self, IOService* provider)
    {
        auto  original = reinterpret_cast<bool (*)(void*, IOService*)>(acceleratorStartOriginal);
        auto* pci      = OSDynamicCast(IOPCIDevice, provider);
        if (!supported() || !pci || WIOKit::readPCIConfigValue(pci, WIOKit::kIOPCIConfigVendorID) != 0x1002
            || WIOKit::readPCIConfigValue(pci, WIOKit::kIOPCIConfigDeviceID) != NRed::singleton().getDeviceID())
        {
            return original(self, provider);
        }
        bool expected = false;
        if (__atomic_compare_exchange_n(&loadAttempted, &expected, true, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            // 在驱动 start 内请求可选加载，避免插件库依赖和加载回调内的递归请求。
            const auto status   = OSKextLoadKextWithIdentifier("com.apple.kext.AMDRadeonX6000");
            const bool complete = status == kOSReturnSuccess && has5000 && has6000 && hooks;
            __atomic_store_n(&loadCompleted, complete, __ATOMIC_RELEASE);
            NRed::singleton().setProp32("NRedVCNNativeLoadStatus", static_cast<UInt32>(status));
            NRed::singleton().setProp32("NRedVCNNativeLoadReady", complete);
            SYSLOG("VCN", "native load status=0x%X prepared=%u", status, complete);
        }
        const auto result = original(self, provider);
        NRed::singleton().setProp32("NRedVCNAcceleratorStarted", result);
        if (!result && !engineAllocated) { __atomic_store_n(&loadCompleted, false, __ATOMIC_RELEASE); }
        publishVP9Probe(self, result);
        return result;
    }

    template<typename T>
    bool resolve(KernelPatcher& patcher, size_t id, const char* name, T& target, mach_vm_address_t slide, size_t size)
    {
        target = reinterpret_cast<T>(patcher.solveSymbol(id, name, slide, size, true));
        patcher.clearError();
        if (!target) {
            SYSLOG("VCN", "missing symbol: %s", name);
            return false;
        }
        return true;
    }

    OSMetaClassBase* safeCast(const OSMetaClassBase* object, const OSMetaClass* target)
    {
        using Cast                             = OSMetaClassBase* (*)(const OSMetaClassBase*, const OSMetaClass*);
        auto                          original = reinterpret_cast<Cast>(safeCastOriginal);
        auto*                         result   = original(object, target);
        if (result || !object || !VCN::ready()) { return result; }
        for (const auto& pair : pairs) {
            const auto* alternative = target == pair.oldClass ? pair.newClass :
                                      target == pair.newClass ? pair.oldClass :
                                                                nullptr;
            if (!alternative) { continue; }
            result = original(object, alternative);
            if (result && result->getMetaClass()->getClassSize() >= target->getClassSize()) { return result; }
        }
        return nullptr;
    }

    bool verifyKernelABI(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size, bool old)
    {
        if (size < 16 || slide > ~mach_vm_address_t{0} - size) { return false; }
        const auto* guards = VCNKernelProfiles::guards(currentKernelVersion().major(), old);
        if (!guards) { return false; }
        static_assert(arrsize(VCNKernelData::Guards5000) == arrsize(VCNKernelData::Guards6000));
        mach_vm_address_t table{0};
        if (!resolve(patcher, id, old ? VCNKernelData::Table5000 : VCNKernelData::Table6000, table, slide, size)) {
            return false;
        }
        for (size_t i = 0; i < arrsize(VCNKernelData::Guards5000); ++i) {
            const auto& guard = guards[i];
            // __ZTV 的两个头部槽不属于对象地址点；先核对边界再读取指针和 getter。
            const auto offset = 2 * sizeof(void*) + guard.slot;
            if (offset > size - sizeof(void*) || table < slide || table - slide > size - sizeof(void*) - offset) {
                return false;
            }
            mach_vm_address_t target{0};
            memcpy(&target, reinterpret_cast<void*>(table + offset), sizeof(target));
            if (target < slide || target - slide >= size || guard.size > size - (target - slide)) { return false; }
            if (guard.target) {
                mach_vm_address_t expected{0};
                if (!resolve(patcher, id, guard.target, expected, slide, size) || target != expected) { return false; }
            }
            else if (!guard.code || !guard.size || memcmp(reinterpret_cast<void*>(target), guard.code, guard.size)) {
                return false;
            }
        }
        return true;
    }

    void* newShared()
    {
        if (VCN::allocated()) { return sharedClass->alloc(); }
        return reinterpret_cast<void* (*)()>(newSharedOriginal)();
    }
    void* newClient()
    {
        if (VCN::allocated()) { return clientClass->alloc(); }
        return reinterpret_cast<void* (*)()>(newClientOriginal)();
    }
    void* newVideo(void* self)
    {
        if (VCN::allocated()) { return reinterpret_cast<void* (*)(void*)>(newVideo6000)(self); }
        return reinterpret_cast<void* (*)(void*)>(newVideoOriginal)(self);
    }
    bool clientStart(void* self, void* provider)
    {
        auto address = VCN::allocated() ? clientStart5000 : clientStart6000;
        return reinterpret_cast<bool (*)(void*, void*)>(address)(self, provider);
    }
    void clientStop(void* self, void* provider)
    {
        auto address = VCN::allocated() ? clientStop5000 : clientStop6000;
        reinterpret_cast<void (*)(void*, void*)>(address)(self, provider);
    }
    UInt64 surfaceCopy(void* self, void* input, UInt64 size, void* event)
    {
        auto address = VCN::allocated() ? copy5000 : copy6000;
        return reinterpret_cast<UInt64 (*)(void*, void*, UInt64, void*)>(address)(self, input, size, event);
    }
    void* createSML(UInt32 configuration)
    {
        // GFX9 专用配置保留原生 GFX9 SML；其他配置使用含 VCN 描述的通用接口。
        if (VCN::ready() && (configuration & 0xFFF8FFFFU) != 0) {
            // 原生 X6000 的宽松掩码会选择 GFX10；最低位只用于选择通用构造器，不传入构造器。
            return reinterpret_cast<void* (*)(UInt32)>(sml6000)(configuration | 1U);
        }
        return reinterpret_cast<void* (*)(UInt32)>(sml5000)(configuration);
    }
    void signalWork(void* hardware)
    {
        if (VCN::allocated()) { notifyAccess(hardware); }
        else {
            reinterpret_cast<void (*)(void*)>(signalOriginal)(hardware);
        }
    }
    bool initHardware(void* self, UInt32 channel)
    {
        const auto result = reinterpret_cast<bool (*)(void*, UInt32)>(initHardwareOriginal)(self, channel);
        NRed::singleton().setProp32("NRedVCNLastHardwareInit", result);
        auto count = __atomic_fetch_add(&hardwareInitCount, 1U, __ATOMIC_RELAXED);
        if (count < 16) { SYSLOG("VCN", "initHardware channel=%u result=%u", channel, result); }
        return result;
    }

    // Queue keep.
    // AMDVCNHWEngine counts the users of each hardware queue group. stopQueue releases a group's ring
    // (releaseHardware, which puts RBC_RB_RPTR/WPTR and the fw_shared copies back to 0) as soon as its count
    // reaches 0, without waiting for the jobs already on that ring; only setSuspendResumeState waits for the last
    // timestamp before the same call. While another group keeps the engine powered (screen recording on the
    // encoder), a browser that opens and closes decoder sessions while a video starts resets the decode ring
    // several times within milliseconds, a job written around such a reset never completes and the VCN hangs.
    // Like amdgpu, which keeps the VCN rings for as long as the block is up, the last release of a group is
    // deferred while another group is active, and done through the original path once the engine goes idle,
    // which then also powers the engine down as before. Without another active group nothing changes.
    constexpr const char* StartQueueSymbol = "__ZN29AMDRadeonX6000_AMDVCNHWEngine10startQueueE21eAMDAccelVideoChannel";
    constexpr const char* StopQueueSymbol  = "__ZN29AMDRadeonX6000_AMDVCNHWEngine9stopQueueE21eAMDAccelVideoChannel";
    constexpr size_t      QueueCounterCount = 6;    // per-group user counters, consecutive, decode first
    constexpr size_t      QueueGroupCount   = 2;
    // Channel used for the deferred release: decode (8) and encode (9; 9 and 10 share one counter and ring pair).
    constexpr UInt32      QueueChannels[QueueGroupCount] = {8, 9};
    size_t                queueCounters[QueueGroupCount]{};
    size_t                queueCounterBase{0};
    bool                  queueKeep{false};
    bool                  queueKept[QueueGroupCount]{};
    UInt32                queueKeptCount{0}, queueReleasedCount{0};
    mach_vm_address_t     startQueueOriginal{0}, stopQueueOriginal{0};

    int queueGroup(const UInt32 channel)
    {
        if (channel == 8) { return 0; }
        if (channel == 9 || channel == 10) { return 1; }
        return -1;
    }

    UInt32& queueUsers(void* engine, const size_t offset)
    {
        return *reinterpret_cast<UInt32*>(static_cast<UInt8*>(engine) + offset);
    }

    // Users of all groups, without the references held for kept rings.
    UInt32 queueActive(void* engine)
    {
        UInt32 total{0}, kept{0};
        for (size_t i = 0; i < QueueCounterCount; ++i) { total += queueUsers(engine, queueCounterBase + i * 4); }
        for (const bool group : queueKept) { kept += group; }
        return total > kept ? total - kept : 0;
    }

    void publishQueueKeep()
    {
        NRed::singleton().setProp32("NRedVCNQueueKept", __atomic_load_n(&queueKeptCount, __ATOMIC_RELAXED));
        NRed::singleton().setProp32("NRedVCNQueueKeptReleased", __atomic_load_n(&queueReleasedCount, __ATOMIC_RELAXED));
    }

    // Callers serialise start/stop (the original counters are plain integers updated without atomics).
    bool startQueue(void* engine, UInt32 channel)
    {
        const auto original = reinterpret_cast<bool (*)(void*, UInt32)>(startQueueOriginal);
        const auto group    = queueGroup(channel);
        const auto result   = original(engine, channel);
        if (result && group >= 0 && engine && queueKept[group]) {
            // The kept ring serves this session; the kept reference becomes the session's own one.
            queueKept[group] = false;
            auto& users      = queueUsers(engine, queueCounters[group]);
            if (users > 1) { users -= 1; }
        }
        return result;
    }

    bool stopQueue(void* engine, UInt32 channel)
    {
        const auto original = reinterpret_cast<bool (*)(void*, UInt32)>(stopQueueOriginal);
        const auto group    = queueGroup(channel);
        if (!queueKeep || group < 0 || !engine || !VCN::allocated()) { return original(engine, channel); }
        auto& users = queueUsers(engine, queueCounters[group]);
        if (!queueKept[group] && users == 1 && queueActive(engine) > 1) {
            // Last session of this group while another group is active: keep the ring.
            queueKept[group] = true;
            __atomic_add_fetch(&queueKeptCount, 1U, __ATOMIC_RELAXED);
            publishQueueKeep();
            return true;
        }
        const auto result = original(engine, channel);
        if (users == 0) { queueKept[group] = false; }
        if (queueActive(engine) == 0) {
            // The engine goes idle: release the kept rings; the last release powers the engine down.
            for (size_t i = 0; i < QueueGroupCount; ++i) {
                if (!queueKept[i]) { continue; }
                queueKept[i] = false;
                original(engine, QueueChannels[i]);
                __atomic_add_fetch(&queueReleasedCount, 1U, __ATOMIC_RELAXED);
            }
            publishQueueKeep();
        }
        return result;
    }

    const UInt8* findBytes(const UInt8* data, size_t length, const UInt8* pattern, size_t count)
    {
        for (size_t i = 0; i + count <= length; ++i) {
            if (!memcmp(data + i, pattern, count)) { return data + i; }
        }
        return nullptr;
    }

    UInt32 read32(const UInt8* data)
    {
        UInt32 value;
        memcpy(&value, data, sizeof(value));
        return value;
    }

    // Reads the counter offsets from the code and checks that stopQueue's idle test covers exactly these six.
    bool readQueueLayout(mach_vm_address_t start, mach_vm_address_t stop, mach_vm_address_t slide, size_t size)
    {
        constexpr size_t Window = 0x100;
        if (size < Window || start < slide || stop < slide || start - slide > size - Window
            || stop - slide > size - Window)
        {
            return false;
        }
        const auto* code = reinterpret_cast<const UInt8*>(start);
        // cmp esi, 8; jne short; mov eax, [rbx + decode]
        static const UInt8 decodeTest[] = {0x83, 0xFE, 0x08, 0x75};
        const auto*        decode       = findBytes(code, 0x40, decodeTest, sizeof(decodeTest));
        if (!decode || decode[5] != 0x8B || decode[6] != 0x83) { return false; }
        // lea eax, [r14 - 9]; cmp eax, 1; ja near; mov eax, [rbx + encode]
        static const UInt8 encodeTest[] = {0x41, 0x8D, 0x46, 0xF7, 0x83, 0xF8, 0x01, 0x0F, 0x87};
        const auto*        encode       = findBytes(code, 0x80, encodeTest, sizeof(encodeTest));
        if (!encode || encode[13] != 0x8B || encode[14] != 0x83) { return false; }
        const size_t base = read32(decode + 7), encodeOffset = read32(encode + 15);
        if (base < 0x20 || base > 0x10000 || encodeOffset < base || encodeOffset >= base + QueueCounterCount * 4
            || (encodeOffset - base) % 4)
        {
            return false;
        }
        // mov eax, [rbx + a]; or eax, [rbx + b] (five times): the idle test over all groups.
        const auto* idle = reinterpret_cast<const UInt8*>(stop);
        for (size_t i = 0; i + QueueCounterCount * 6 <= Window; ++i) {
            UInt32 seen = 0;
            for (size_t k = 0; k < QueueCounterCount; ++k) {
                const auto* op = idle + i + k * 6;
                if (op[0] != (k ? 0x0B : 0x8B) || op[1] != 0x83) { break; }
                const size_t offset = read32(op + 2);
                if (offset < base || offset >= base + QueueCounterCount * 4 || (offset - base) % 4) { break; }
                seen |= 1U << ((offset - base) / 4);
            }
            if (seen == (1U << QueueCounterCount) - 1) {
                queueCounterBase = base;
                queueCounters[0] = base;
                queueCounters[1] = encodeOffset;
                return true;
            }
        }
        return false;
    }

    void installQueueKeep(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
    {
        if (checkKernelArgument("-NRedNoVCNQueueKeep")) {
            NRed::singleton().setProp32("NRedVCNQueueKeep", 0);
            return;
        }
        mach_vm_address_t start{0}, stop{0};
        if (!resolve(patcher, id, StartQueueSymbol, start, slide, size)
            || !resolve(patcher, id, StopQueueSymbol, stop, slide, size) || !readQueueLayout(start, stop, slide, size))
        {
            NRed::singleton().setProp32("NRedVCNQueueKeep", 2);
            SYSLOG("VCN", "queue keep: unknown AMDVCNHWEngine layout");
            return;
        }
        KernelPatcher::RouteRequest routes[] = {
            {StartQueueSymbol, startQueue, startQueueOriginal},
            {StopQueueSymbol, stopQueue, stopQueueOriginal},
        };
        if (!patcher.routeMultiple(id, routes, arrsize(routes), slide, size)) {
            patcher.clearError();
            NRed::singleton().setProp32("NRedVCNQueueKeep", 3);
            SYSLOG("VCN", "queue keep: route failed");
            return;
        }
        queueKeep = true;
        NRed::singleton().setProp32("NRedVCNQueueKeep", 1);
        NRed::singleton().setProp32("NRedVCNQueueCounters", static_cast<UInt32>(queueCounterBase));
        publishQueueKeep();
    }

    bool install(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
    {
        if (size < 6 || slide > ~mach_vm_address_t{0} - size) { return false; }
        size_t      patchCount{0};
        const auto* patches = VCNKernelProfiles::patches(currentKernelVersion().major(), patchCount);
        if (!patches || patchCount == 0 || patchCount > 32) { return false; }
        mach_vm_address_t addresses[32]{};
        mach_vm_address_t signal{0};
        if (!resolve(patcher, id, "__ZN26AMDRadeonX6000_AMDHardware22signalGPUWorkSubmittedEv", signal, slide, size)) {
            return false;
        }
        for (size_t i = 0; i < patchCount; ++i) {
            const auto&       patch = patches[i];
            mach_vm_address_t function{0};
            if (!resolve(patcher, id, patch.symbol, function, slide, size)) { return false; }
            addresses[i] = function + patch.offset;
            if (patch.newSlot == 0) {
                const auto next = addresses[i] + 5;
                if (next < addresses[i]
                    || (signal >= next ? signal - next > 0x7FFFFFFFULL : next - signal > 0x80000000ULL))
                {
                    return false;
                }
            }
            if (function > ~mach_vm_address_t{0} - patch.offset || addresses[i] < slide
                || addresses[i] > slide + size - sizeof(patch.original)
                || memcmp(reinterpret_cast<void*>(addresses[i]), patch.original, sizeof(patch.original)))
            {
                SYSLOG("VCN", "kernel code mismatch: %s+0x%X", patch.symbol, patch.offset);
                return false;
            }
        }
        if (MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) != KERN_SUCCESS) { return false; }
        for (size_t i = 0; i < patchCount; ++i) {
            const auto& patch = patches[i];
            UInt8       replacement[6];
            memcpy(replacement, patch.original, sizeof(replacement));
            if (patch.newSlot) { memcpy(replacement + 2, &patch.newSlot, sizeof(patch.newSlot)); }
            else {
                const auto displacement = static_cast<SInt32>(signal - addresses[i] - 5);
                replacement[0]          = 0xE8;
                memcpy(replacement + 1, &displacement, sizeof(displacement));
                replacement[5] = 0x90;
            }
            memcpy(reinterpret_cast<void*>(addresses[i]), replacement, sizeof(replacement));
        }
        MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock);
        KernelPatcher::RouteRequest routes[] = {
            {"__ZN26AMDRadeonX6000_AMDHardware22signalGPUWorkSubmittedEv", signalWork, signalOriginal},
            {"__ZN39AMDRadeonX6000_AMDAccelSharedUserClient5startEP9IOService", clientStart, clientStart6000},
            {"__ZN39AMDRadeonX6000_AMDAccelSharedUserClient4stopEP9IOService", clientStop, clientStop6000},
            {"__ZN29AMDRadeonX6000_AMDAccelShared11SurfaceCopyEPjyP12IOAccelEvent", surfaceCopy, copy6000},
            {"__ZN30AMDRadeonX6000_AMDVCN2HWEngine12initHardwareE21eAMDAccelVideoChannel", initHardware,
             initHardwareOriginal},
        };
        if (!patcher.routeMultiple(id, routes, arrsize(routes), slide, size)) {
            patcher.clearError();
            if (MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) == KERN_SUCCESS) {
                for (size_t i = 0; i < patchCount; ++i) {
                    memcpy(reinterpret_cast<void*>(addresses[i]), patches[i].original, 6);
                }
                MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock);
            }
            return false;
        }
        return true;
    }

}    // namespace

void VCN::setVP9Ready(const bool available)
{
    if (!requested) { return; }
    const bool previous = __atomic_exchange_n(&vp9BackendReady, available, __ATOMIC_ACQ_REL);
    if (previous == available) { return; }
    auto* matching = IOService::serviceMatching("AMDRadeonX5000_AMDGraphicsAccelerator");
    auto* services = matching ? IOService::getMatchingServices(matching) : nullptr;
    OSSafeReleaseNULL(matching);
    if (!services) { return; }
    while (auto* service = OSDynamicCast(IOService, services->getNextObject())) {
        auto* pci = OSDynamicCast(IOPCIDevice, service->getProvider());
        if (pci && WIOKit::readPCIConfigValue(pci, WIOKit::kIOPCIConfigVendorID) == 0x1002
            && WIOKit::readPCIConfigValue(pci, WIOKit::kIOPCIConfigDeviceID) == NRed::singleton().getDeviceID())
        {
            publishVP9Probe(service, true);
        }
    }
    services->release();
}

void VCN::init()
{
    requested = checkKernelArgument("-NRedVideoFix");
    if (requested) { lilu.onKextLoadForce(&x6000); }
}
bool VCN::ready()
{
    return supported() && __atomic_load_n(&loadCompleted, __ATOMIC_ACQUIRE) && has5000 && has6000 && hooks;
}
bool  VCN::allocated() { return ready() && engineAllocated; }
void* VCN::allocateEngine()
{
    if (!ready()) {
        NRed::singleton().setProp32("NRedVCNAllocationSkipped", requested);
        return nullptr;
    }
    auto* engine    = engineClass->alloc();
    engineAllocated = engine != nullptr;
    if (!engineAllocated) { __atomic_store_n(&loadCompleted, false, __ATOMIC_RELEASE); }
    NRed::singleton().setProp32("NRedVCNEngineAllocated", engineAllocated);
    return engine;
}

void VCN::processKext(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
{
    if (!supported()) { return; }
    const auto old = id == kextRadeonX5000.loadIndex;
    if (!old && id != x6000.loadIndex) { return; }
    if (!verifyKernelABI(patcher, id, slide, size, old)) {
        if (old) { has5000 = false; }
        else {
            has6000 = hooks = false;
        }
        __atomic_store_n(&loadCompleted, false, __ATOMIC_RELEASE);
        NRed::singleton().setProp32("NRedVCNKernelABIRejected", 1);
        NRed::singleton().setProp32(old ? "NRedVCNX5000Prepared" : "NRedVCNX6000Prepared", 0);
        SYSLOG("VCN", "%s ABI mismatch; bridge disabled", old ? "X5000" : "X6000");
        return;
    }
    NRed::singleton().setProp32(old ? "NRedVCNX5000ABIValidated" : "NRedVCNX6000ABIValidated", 1);
    bool ok = true;
    for (size_t i = 0; i < arrsize(classes); ++i) {
        char name[112];
        snprintf(name, sizeof(name), "__ZN%uAMDRadeonX%s_%s10gMetaClassE", lengths[i], old ? "5000" : "6000",
                 classes[i]);
        auto& target  = old ? pairs[i].oldClass : pairs[i].newClass;
        ok           &= resolve(patcher, id, name, target, slide, size);
    }
    if (old) {
        ok &= resolve(patcher, id, "__ZN39AMDRadeonX5000_AMDAccelSharedUserClient5startEP9IOService", clientStart5000,
                      slide, size);
        ok &= resolve(patcher, id, "__ZN39AMDRadeonX5000_AMDAccelSharedUserClient4stopEP9IOService", clientStop5000,
                      slide, size);
        ok &= resolve(patcher, id, "__ZN29AMDRadeonX5000_AMDAccelShared11SurfaceCopyEPjyP12IOAccelEvent", copy5000,
                      slide, size);
        ok &=
            resolve(patcher, id, "__ZN30AMDRadeonX5000_AMDGFX9Hardware15notifyGfxAccessEv", notifyAccess, slide, size);
        KernelPatcher::RouteRequest routes[] = {
            {"__ZN37AMDRadeonX5000_AMDGraphicsAccelerator5startEP9IOService", acceleratorStart,
             acceleratorStartOriginal},
            {"__ZN37AMDRadeonX5000_AMDGraphicsAccelerator9newSharedEv", newShared, newSharedOriginal},
            {"__ZN37AMDRadeonX5000_AMDGraphicsAccelerator19newSharedUserClientEv", newClient, newClientOriginal},
            {"__ZN41AMDRadeonX5000_AMDGFX9GraphicsAccelerator15newVideoContextEv", newVideo, newVideoOriginal},
            {"__ZN31AMDRadeonX5000_IAMDSMLInterface18createSMLInterfaceEj", createSML, sml5000},
        };
        if (ok) {
            ok = patcher.routeMultiple(id, routes, arrsize(routes), slide, size);
            patcher.clearError();
        }
        has5000 = ok;
        if (ok) {
            KernelPatcher::RouteRequest route{"__ZN15OSMetaClassBase12safeMetaCastEPKS_PK11OSMetaClass", safeCast,
                                              safeCastOriginal};
            ok = patcher.routeMultipleLong(KernelPatcher::KernelID, &route, 1);
            patcher.clearError();
            has5000 = ok;
        }
    }
    else {
        ok &= resolve(patcher, id, "__ZN30AMDRadeonX6000_AMDVCN2HWEngine10gMetaClassE", engineClass, slide, size);
        ok &= resolve(patcher, id, "__ZN29AMDRadeonX6000_AMDAccelShared10gMetaClassE", sharedClass, slide, size);
        ok &= resolve(patcher, id, "__ZN39AMDRadeonX6000_AMDAccelSharedUserClient10gMetaClassE", clientClass, slide,
                      size);
        ok &= resolve(patcher, id, "__ZN42AMDRadeonX6000_AMDGFX10GraphicsAccelerator15newVideoContextEv", newVideo6000,
                      slide, size);
        ok &= resolve(patcher, id, "__ZN31AMDRadeonX6000_IAMDSMLInterface18createSMLInterfaceEj", sml6000, slide, size);
        if (ok) { ok = install(patcher, id, slide, size); }
        if (ok) { installQueueKeep(patcher, id, slide, size); }
        has6000 = hooks = ok;
        NRed::singleton().setProp32("NRedVCNIdleGfxOffFix", ok);
    }
    NRed::singleton().setProp32(old ? "NRedVCNX5000Prepared" : "NRedVCNX6000Prepared", ok);
    SYSLOG("VCN", "%s prepared=%u ready=%u", old ? "X5000" : "X6000", ok, ready());
}
