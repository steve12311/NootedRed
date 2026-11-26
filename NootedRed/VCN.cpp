// 复用原生 VCN2 引擎和客户端，修正已核实的跨驱动调用。
#include <Headers/kern_api.hpp>
#include <Headers/kern_iokit.hpp>
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <IOKit/pci/IOPCIDevice.h>
#include <kern/clock.h>
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

    // Minimum on-time.
    // releaseHardware stops a queue in CAIL (vcn_stop_queue -> engine_2_0_dpg_uninitialize), which unpauses DPG and
    // waits for UVD_POWER_STATUS to report the tiles gated. When that comes a few ms after the queue was started
    // (players and browsers open and immediately close decoder sessions while probing), the firmware sometimes keeps
    // the tiles on: the wait times out after 5 s, the engine is left half stopped and the next decode job never
    // completes. amdgpu never stops VCN that soon after starting it (it gates after 1 s idle). A release that comes
    // less than MinOnMs after the last initHardware waits for the rest of that time first.
    constexpr UInt32  MinOnMs = 50;
    bool              minOnTime{false};
    UInt64            lastHardwareInit{0};
    UInt32            minOnDelays{0};
    mach_vm_address_t releaseHardwareOriginal{0};

    UInt64 nowNs()
    {
        UInt64 ns{0};
        absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
        return ns;
    }

    bool releaseHardware(void* self, UInt32 channel)
    {
        if (minOnTime && VCN::allocated()) {
            const auto since = nowNs() - __atomic_load_n(&lastHardwareInit, __ATOMIC_ACQUIRE);
            if (since < MinOnMs * 1000000ULL) {
                const auto delays = __atomic_add_fetch(&minOnDelays, 1U, __ATOMIC_RELAXED);
                IOSleep(static_cast<UInt32>((MinOnMs * 1000000ULL - since + 999999ULL) / 1000000ULL));
                NRed::singleton().setProp32("NRedVCNMinOnDelays", delays);
            }
        }
        return reinterpret_cast<bool (*)(void*, UInt32)>(releaseHardwareOriginal)(self, channel);
    }

    void installMinOnTime(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
    {
        if (checkKernelArgument("-NRedNoVCNMinOn")) {
            NRed::singleton().setProp32("NRedVCNMinOn", 0);
            return;
        }
        constexpr const char* symbol = "__ZN30AMDRadeonX6000_AMDVCN2HWEngine15releaseHardwareE21eAMDAccelVideoChannel";
        // Short jump: only `add esi, -8; cmp esi, 2` (6 bytes) go to the trampoline, not the relative `ja` after them,
        // which a 14-byte jump would copy with a wrong target.
        constexpr UInt8 prologue[] = {0x83, 0xC6, 0xF8, 0x83, 0xFE, 0x02, 0x77};
        const auto      target     = patcher.solveSymbol(id, symbol, slide, size, true);
        patcher.clearError();
        KernelPatcher::RouteRequest route{symbol, releaseHardware, releaseHardwareOriginal};
        minOnTime = target != 0 && size >= sizeof(prologue) && target >= slide
                 && target - slide <= size - sizeof(prologue)
                 && !memcmp(reinterpret_cast<const void*>(target), prologue, sizeof(prologue))
                 && patcher.routeMultipleShort(id, &route, 1, slide, size);
        patcher.clearError();
        NRed::singleton().setProp32("NRedVCNMinOn", minOnTime ? MinOnMs : 0xFFFFFFFF);
    }

    bool initHardware(void* self, UInt32 channel)
    {
        const auto result = reinterpret_cast<bool (*)(void*, UInt32)>(initHardwareOriginal)(self, channel);
        if (result) { __atomic_store_n(&lastHardwareInit, nowNs(), __ATOMIC_RELEASE); }
        NRed::singleton().setProp32("NRedVCNLastHardwareInit", result);
        auto count = __atomic_fetch_add(&hardwareInitCount, 1U, __ATOMIC_RELAXED);
        if (count < 16) { SYSLOG("VCN", "initHardware channel=%u result=%u", channel, result); }
        return result;
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
        if (ok) { installMinOnTime(patcher, id, slide, size); }
        has6000 = hooks = ok;
        NRed::singleton().setProp32("NRedVCNIdleGfxOffFix", ok);
    }
    NRed::singleton().setProp32(old ? "NRedVCNX5000Prepared" : "NRedVCNX6000Prepared", ok);
    SYSLOG("VCN", "%s prepared=%u ready=%u", old ? "X5000" : "X6000", ok, ready());
}
