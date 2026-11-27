// 复用原生 VCN2 引擎和客户端，修正已核实的跨驱动调用。
#include <Headers/kern_api.hpp>
#include <Headers/kern_iokit.hpp>
#include <Headers/kern_util.hpp>
#include <IOKit/IOLib.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/pci/IOPCIDevice.h>
#include <kern/clock.h>
#include <Kexts.hpp>
#include <NRed.hpp>
#include <PenguinWizardry/KernelVersion.hpp>
#include <VCN.hpp>
#include <VCNCapabilities.hpp>
#include <VCNKernelData.hpp>
#include <VCNKernelProfiles.hpp>
#include <VCNQueueDrain.hpp>
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
    const char* ioAccelPath = "/System/Library/Extensions/IOAcceleratorFamily2.kext/Contents/MacOS/IOAcceleratorFamily2";
    KernelPatcher::KextInfo ioAccel{"com.apple.iokit.IOAcceleratorFamily2", &ioAccelPath, 1, {true}, {},
                                    KernelPatcher::KextInfo::Unloaded};
    bool                    requested{false}, has5000{false}, has6000{false}, hooks{false}, engineAllocated{false};
    bool                    loadAttempted{false}, loadCompleted{false};
    mach_vm_address_t       acceleratorStartOriginal{0};
    mach_vm_address_t       safeCastOriginal{0}, newSharedOriginal{0}, newClientOriginal{0}, newVideoOriginal{0};
    mach_vm_address_t       newVideo6000{0};
    mach_vm_address_t       clientStart5000{0}, clientStop5000{0}, clientStart6000{0}, clientStop6000{0};
    mach_vm_address_t       copy5000{0}, copy6000{0}, sml5000{0}, sml6000{0};
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
    bool                  queueDrainRouted{false};
    void*                 queueDrainEngine{nullptr};
    VCNQueueDrain::Layout queueDrainLayout{};
    mach_vm_address_t     stopQueueOriginal{0}, replay5000{0}, wait5000{0}, accelLookup{0};
    size_t                replay5000Size{0};
    UInt32                hardwareWorkLoopOwner{0};
    IOWorkLoop*           (*hardwareWorkLoop)(const IOService*){nullptr};
    constexpr char        StopQueueSymbol[] = "__ZN29AMDRadeonX6000_AMDVCNHWEngine9stopQueueE21eAMDAccelVideoChannel";

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
    template<typename T>
    const T& queueField(const void* object, size_t offset)
    {
        return *reinterpret_cast<const T*>(static_cast<const UInt8*>(object) + offset);
    }

    mach_vm_address_t queueSlot(const void* object, size_t slot)
    {
        return queueField<mach_vm_address_t>(queueField<void*>(object, 0), slot);
    }

    enum class QueueDrainResult
    {
        Skipped,
        Completed,
        TimestampChanged
    };

    // 原生 stop 的调用方可能持有 accelerator 的 busy 锁，取得硬件 gate 时不能阻塞。
    struct QueueWorkLoop : IOWorkLoop
    {
        static bool tryEnter(IOWorkLoop* loop)
        {
            return (loop->*&QueueWorkLoop::tryCloseGate)();
        }
        static void leave(IOWorkLoop* loop) { (loop->*&QueueWorkLoop::openGate)(); }
    };

    struct QueueGate
    {
        IOWorkLoop* loop;
        bool        entered{false};

        bool enter() { return loop && (loop->inGate() || (entered = QueueWorkLoop::tryEnter(loop))); }
        void leave()
        {
            if (entered) { QueueWorkLoop::leave(loop); }
            entered = false;
        }
    };

    OSMetaClassBase* getQueueChannel(void* engine, IOService* accelerator, UInt32 slot)
    {
        const auto& layout = queueDrainLayout;
        auto* hw = queueField<void*>(engine, layout.channels[slot]);
        if (!hw) { return nullptr; }
        const auto getIndex = reinterpret_cast<SInt32 (*)(void*)>(queueSlot(hw, layout.getChannelIndex));
        if (!getIndex) { return nullptr; }
        const auto index = getIndex(hw);
        if (index < 0) { return nullptr; }
        const auto lookup = reinterpret_cast<OSMetaClassBase* (*)(void*, UInt32)>(accelLookup);
        auto* candidate = lookup(accelerator, static_cast<UInt32>(index));
        if (!candidate) { return nullptr; }
        const auto cast = reinterpret_cast<OSMetaClassBase* (*)(const OSMetaClassBase*, const OSMetaClass*)>(
            safeCastOriginal);
        return cast(candidate, pairs[2].oldClass);
    }

    QueueDrainResult waitForQueue(void* engine, IOService* accelerator, UInt32 channel, QueueGate& gate)
    {
        const auto& layout = queueDrainLayout;
        const auto first = channel == VCNQueueDrain::DecodeChannel ? 0U : 1U;
        if (queueField<UInt8>(engine, 0x10) != 1 || queueField<UInt32>(engine, layout.users[first]) != 1) {
            return QueueDrainResult::Skipped;
        }
        const auto end = first == 0 ? 1U : 3U;
        const auto wait = reinterpret_cast<bool (*)(void*, UInt32)>(wait5000);
        OSObject* channels[3]{};
        UInt32 submitted[3]{};
        for (auto i = first; i < end; ++i) {
            channels[i] = static_cast<OSObject*>(getQueueChannel(engine, accelerator, i));
            if (!channels[i]) { return QueueDrainResult::Skipped; }
            submitted[i] = queueField<UInt32>(channels[i], layout.submitted);
        }
        // 等待期间让硬件工作循环处理完成事件。原生调用方已持有的 gate 仍由调用方管理。
        for (auto i = first; i < end; ++i) { channels[i]->retain(); }
        gate.leave();
        auto drain = QueueDrainResult::Completed;
        for (auto i = first; i < end; ++i) {
            if (!wait(channels[i], submitted[i])) {
                drain = QueueDrainResult::Skipped;
                break;
            }
        }
        if (drain == QueueDrainResult::Completed) {
            if (!gate.enter()) { drain = QueueDrainResult::Skipped; }
            else if (engine != __atomic_load_n(&queueDrainEngine, __ATOMIC_ACQUIRE)
                     || queueField<IOService*>(engine, layout.accelerator) != accelerator
                     || !queueField<void*>(accelerator, hardwareWorkLoopOwner)
                     || hardwareWorkLoop(accelerator) != gate.loop) { drain = QueueDrainResult::Skipped; }
            else {
                for (auto i = first; i < end; ++i) {
                    if (getQueueChannel(engine, accelerator, i) != channels[i]) {
                        drain = QueueDrainResult::Skipped;
                        break;
                    }
                    if (queueField<UInt32>(channels[i], layout.submitted) != submitted[i]) {
                        drain = QueueDrainResult::TimestampChanged;
                        break;
                    }
                }
            }
        }
        for (auto i = first; i < end; ++i) { channels[i]->release(); }
        return drain;
    }

    bool stopQueue(void* engine, UInt32 channel)
    {
        const auto original = reinterpret_cast<bool (*)(void*, UInt32)>(stopQueueOriginal);
        if (channel < VCNQueueDrain::DecodeChannel || channel > VCNQueueDrain::EncodeChannel + 1
            || !engine || engine != __atomic_load_n(&queueDrainEngine, __ATOMIC_ACQUIRE)) {
            return original(engine, channel);
        }
        auto* accelerator = queueField<IOService*>(engine, queueDrainLayout.accelerator);
        const auto cast = reinterpret_cast<OSMetaClassBase* (*)(const OSMetaClassBase*, const OSMetaClass*)>(
            safeCastOriginal);
        QueueGate gate{nullptr};
        if (hardwareWorkLoop && accelerator && cast(accelerator, pairs[0].oldClass)
            && queueField<void*>(accelerator, hardwareWorkLoopOwner)
            && queueSlot(accelerator, queueDrainLayout.getAccelChannel) == accelLookup) {
            gate.loop = hardwareWorkLoop(accelerator);
        }
        if (gate.loop && accelerator) {
            gate.loop->retain();
            accelerator->retain();
        }
        auto drain = gate.enter() ? waitForQueue(engine, accelerator, channel, gate) : QueueDrainResult::Skipped;
        if (drain == QueueDrainResult::TimestampChanged) { drain = waitForQueue(engine, accelerator, channel, gate); }
        if (drain != QueueDrainResult::Completed) { gate.leave(); }
        const auto result = original(engine, channel);
        gate.leave();
        if (gate.loop && accelerator) {
            gate.loop->release();
            accelerator->release();
        }
        return result;
    }

    void prepareQueueDrain5000(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
    {
        resolve(patcher, id, "__ZN30AMDRadeonX5000_AMDAccelChannel16waitForTimestampEj", wait5000, slide, size);
        if (wait5000 < slide || wait5000 - slide >= size) { wait5000 = 0; }
        if (resolve(patcher, id, "__ZN30AMDRadeonX5000_AMDAccelChannel22resetHardwareAndReplayEv", replay5000, slide, size)
            && replay5000 >= slide && replay5000 - slide < size) {
            const auto available = size - (replay5000 - slide);
            replay5000Size = available < 0x280 ? available : 0x280;
        }
    }

    void prepareQueueDrainWorkLoop(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
    {
        constexpr UInt8 loadOwner[] = {0x55, 0x48, 0x89, 0xE5, 0x48, 0x8B, 0x87};
        constexpr UInt8 loadLoop[] = {0x48, 0x8B, 0x40, 0x58, 0x5D, 0xC3};
        constexpr size_t GetterSize = sizeof(loadOwner) + sizeof(UInt32) + sizeof(loadLoop);
        mach_vm_address_t getter{0};
        if (size < GetterSize
            || !resolve(patcher, id, "__ZNK22IOGraphicsAccelerator219getHardwareWorkLoopEv", getter, slide, size)
            || getter < slide || getter - slide > size - GetterSize) { return; }
        const auto* code = reinterpret_cast<const UInt8*>(getter);
        const auto owner = VCNQueueDrain::read32(code + sizeof(loadOwner));
        if (memcmp(code, loadOwner, sizeof(loadOwner)) || memcmp(code + GetterSize - sizeof(loadLoop), loadLoop,
                                                               sizeof(loadLoop))
            || owner < sizeof(void*) || owner > 0xFFF8 || owner % sizeof(void*)) { return; }
        hardwareWorkLoopOwner = owner;
        hardwareWorkLoop = reinterpret_cast<decltype(hardwareWorkLoop)>(getter);
    }

    bool installQueueDrain(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
    {
        constexpr size_t StopSize = 0x100, SuspendSize = 0x1CA, LookupSlot = 0x1A0;
        mach_vm_address_t stop{0}, suspend{0}, wait6000{0}, acceleratorTable{0};
        if (!hardwareWorkLoop || !wait5000 || !replay5000Size || size < SuspendSize) {
            return false;
        }
        if (!resolve(patcher, id, StopQueueSymbol, stop, slide, size)
            || !resolve(patcher, id, "__ZN29AMDRadeonX6000_AMDVCNHWEngine21setSuspendResumeStateEb", suspend, slide, size)
            || !resolve(patcher, id, "__ZN30AMDRadeonX6000_AMDAccelChannel16waitForTimestampEj", wait6000, slide, size)
            || !resolve(patcher, id, "__ZTV42AMDRadeonX6000_AMDGFX10GraphicsAccelerator", acceleratorTable, slide, size)
            || stop < slide || stop - slide > size - StopSize
            || suspend < slide || suspend - slide > size - SuspendSize
            || acceleratorTable < slide || acceleratorTable - slide > size - LookupSlot - 3 * sizeof(void*)) {
            return false;
        }
        VCNQueueDrain::Layout layout{};
        const auto* wait = reinterpret_cast<const UInt8*>(wait6000);
        if (!VCNQueueDrain::readStop(reinterpret_cast<const UInt8*>(stop), StopSize, layout)
            || !VCNQueueDrain::readSuspend(reinterpret_cast<const UInt8*>(suspend), SuspendSize, wait, layout)
            || !VCNQueueDrain::readsSubmittedStamp(reinterpret_cast<const UInt8*>(replay5000), replay5000Size,
                                                  reinterpret_cast<const UInt8*>(wait5000), layout.submitted)) {
            return false;
        }
        queueDrainLayout = layout;
        accelLookup = queueField<mach_vm_address_t>(reinterpret_cast<const void*>(acceleratorTable),
                                                   2 * sizeof(void*) + LookupSlot);
        if (!accelLookup) { return false; }
        KernelPatcher::RouteRequest route{StopQueueSymbol, stopQueue, stopQueueOriginal};
        const auto installed = patcher.routeMultiple(id, &route, 1, slide, size);
        patcher.clearError();
        return installed;
    }

    bool queueDrainLayoutFits()
    {
        const auto engineSize = engineClass->getClassSize();
        return queueDrainLayout.accelerator + sizeof(void*) <= engineSize
            && queueDrainLayout.channels[2] + sizeof(void*) <= engineSize
            && queueDrainLayout.users[1] + sizeof(UInt32) <= engineSize
            && hardwareWorkLoopOwner + sizeof(void*) <= pairs[0].oldClass->getClassSize()
            && queueDrainLayout.submitted + sizeof(UInt32) <= pairs[2].oldClass->getClassSize();
    }

    constexpr UInt32 MinOnMs = 50;
    constexpr UInt64 NsPerMs = 1000000;
    constexpr size_t FreeSlot = 0x90, InitSlot = 0x2D0, ReleaseSlot = 0x2D8;
    constexpr UInt8 StopReleaseCall[] = {0xFF, 0x90, 0xD8, 0x02, 0, 0};
    using HardwareAction = bool (*)(void*, UInt32);
    HardwareAction initHardwareOriginal{nullptr}, releaseHardwareOriginal{nullptr};
    void (*freeEngineOriginal)(void*){nullptr};
    using ThreadCheck = boolean_t (*)();
    ThreadCheck interruptsEnabled{nullptr}, onInterrupt{nullptr}, preemptionEnabled{nullptr};
    mach_vm_address_t engineVtable{0}, stopQueueStart{0}, stopQueueEnd{0};
    void* bridgedEngine{nullptr};
    UInt64 lastHardwareInit{0};
    UInt32 minOnDelays{0};

    UInt64 nowNs()
    {
        UInt64 ns{0};
        absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
        return ns;
    }

    void bindEngine(void* engine)
    {
        if (!engine || !engineVtable || *static_cast<const mach_vm_address_t*>(engine) != engineVtable) { return; }
        void* empty{nullptr};
        __atomic_compare_exchange_n(&bridgedEngine, &empty, engine, false, __ATOMIC_RELEASE, __ATOMIC_RELAXED);
    }

    void freeEngine(void* self)
    {
        if (self == __atomic_load_n(&bridgedEngine, __ATOMIC_ACQUIRE)) {
            __atomic_store_n(&lastHardwareInit, 0, __ATOMIC_RELAXED);
            __atomic_store_n(&bridgedEngine, nullptr, __ATOMIC_RELEASE);
        }
        freeEngineOriginal(self);
    }

    bool initHardware(void* self, UInt32 channel)
    {
        const auto result = initHardwareOriginal(self, channel);
        if (self != __atomic_load_n(&bridgedEngine, __ATOMIC_ACQUIRE)) { return result; }
        if (result && releaseHardwareOriginal) {
            const auto stamp = nowNs();
            auto previous = __atomic_load_n(&lastHardwareInit, __ATOMIC_RELAXED);
            while (previous < stamp
                   && !__atomic_compare_exchange_n(&lastHardwareInit, &previous, stamp, true,
                                                   __ATOMIC_RELEASE, __ATOMIC_RELAXED)) {}
        }
        NRed::singleton().setProp32("NRedVCNLastHardwareInit", result);
        const auto count = __atomic_fetch_add(&hardwareInitCount, 1U, __ATOMIC_RELAXED);
        if (count < 16) { SYSLOG("VCN", "initHardware channel=%u result=%u", channel, result); }
        return result;
    }

    bool stopQueueRelease(mach_vm_address_t caller, mach_vm_address_t start, mach_vm_address_t end)
    {
        return caller >= start && caller - start >= sizeof(StopReleaseCall) && caller < end
            && !memcmp(reinterpret_cast<const void*>(caller - sizeof(StopReleaseCall)), StopReleaseCall,
                       sizeof(StopReleaseCall));
    }

    bool waitForMinOn(void* self, UInt32 channel, mach_vm_address_t caller)
    {
        // 原生系统休眠路径也会直接调用 releaseHardware。只在常规 stopQueue 路径等待。
        if (channel < 8 || channel > 10 || self != __atomic_load_n(&bridgedEngine, __ATOMIC_ACQUIRE)
            || !stopQueueRelease(caller, stopQueueStart, stopQueueEnd)
            || !interruptsEnabled() || onInterrupt() || !preemptionEnabled()) { return false; }

        bool delayed{false};
        for (;;) {
            const auto lastInit = __atomic_load_n(&lastHardwareInit, __ATOMIC_ACQUIRE);
            const auto now = nowNs();
            if (!lastInit || now < lastInit || now - lastInit >= MinOnMs * NsPerMs) { return delayed; }
            IOSleep(static_cast<UInt32>((MinOnMs * NsPerMs - (now - lastInit) + NsPerMs - 1) / NsPerMs));
            delayed = true;
        }
    }

    bool releaseHardware(void* self, UInt32 channel)
    {
        const auto caller = reinterpret_cast<mach_vm_address_t>(__builtin_return_address(0));
        const auto delayed = waitForMinOn(self, channel, caller);
        const auto result = releaseHardwareOriginal(self, channel);
        if (delayed) {
            const auto count = __atomic_add_fetch(&minOnDelays, 1U, __ATOMIC_RELAXED);
            NRed::singleton().setProp32("NRedVCNMinOnDelays", count);
        }
        return result;
    }

    UInt32 installMinOnTime(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size, bool disabled)
    {
        constexpr char Table[] = "__ZTV30AMDRadeonX6000_AMDVCN2HWEngine";
        constexpr char Free[] = "__ZN30AMDRadeonX6000_AMDVCN2HWEngine4freeEv";
        constexpr char Init[] = "__ZN30AMDRadeonX6000_AMDVCN2HWEngine12initHardwareE21eAMDAccelVideoChannel";
        constexpr char Release[] = "__ZN30AMDRadeonX6000_AMDVCN2HWEngine15releaseHardwareE21eAMDAccelVideoChannel";
        constexpr char Stop[] = "__ZN29AMDRadeonX6000_AMDVCNHWEngine9stopQueueE21eAMDAccelVideoChannel";
        constexpr char Suspend[] = "__ZN29AMDRadeonX6000_AMDVCNHWEngine21setSuspendResumeStateEb";
        constexpr size_t TableHeader = 2 * sizeof(mach_vm_address_t);
        constexpr size_t TableSize = TableHeader + ReleaseSlot + sizeof(mach_vm_address_t);
        constexpr size_t DecodeReleaseReturn = 0x36;
        const UInt32 fallback = disabled ? 0 : ~UInt32{0};

        mach_vm_address_t table{0}, free{0}, init{0}, release{0}, stop{0}, suspend{0};
        if (size < TableSize || slide > ~mach_vm_address_t{0} - size
            || !resolve(patcher, id, Table, table, slide, size)
            || table < slide || table - slide > size - TableSize || table % sizeof(mach_vm_address_t)
            || !resolve(patcher, id, Free, free, slide, size) || !resolve(patcher, id, Init, init, slide, size)) {
            return fallback;
        }

        // 直接核对原生虚表槽。`initHardware` 和 `releaseHardware` 的入口都含相对跳转。
        const auto vt = table + TableHeader;
        auto& freeSlot = *reinterpret_cast<mach_vm_address_t*>(vt + FreeSlot);
        auto& initSlot = *reinterpret_cast<mach_vm_address_t*>(vt + InitSlot);
        auto& releaseSlot = *reinterpret_cast<mach_vm_address_t*>(vt + ReleaseSlot);
        if (freeSlot != free || initSlot != init) { return fallback; }

        bool enable = !disabled && resolve(patcher, id, Release, release, slide, size) && releaseSlot == release
            && resolve(patcher, id, Stop, stop, slide, size) && resolve(patcher, id, Suspend, suspend, slide, size)
            && suspend > stop && suspend - stop > DecodeReleaseReturn && suspend - stop <= 0x200
            && stopQueueRelease(stop + DecodeReleaseReturn, stop, suspend);
        if (enable) {
            const auto resolveCheck = [&patcher](const char* symbol) {
                const auto check = reinterpret_cast<ThreadCheck>(patcher.solveSymbol(KernelPatcher::KernelID, symbol));
                patcher.clearError();
                return check;
            };
            interruptsEnabled = resolveCheck("_ml_get_interrupts_enabled");
            onInterrupt = resolveCheck("_ml_at_interrupt_context");
            preemptionEnabled = resolveCheck("_preemption_enabled");
            enable = interruptsEnabled && onInterrupt && preemptionEnabled;
        }

        if (MachInfo::setKernelWriting(true, KernelPatcher::kernelWriteLock) != KERN_SUCCESS) { return fallback; }
        // 写入前再次核对槽位，以免覆盖其他补丁。
        if (freeSlot != free || initSlot != init || (enable && releaseSlot != release)) {
            MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock);
            return fallback;
        }
        freeEngineOriginal = reinterpret_cast<decltype(freeEngineOriginal)>(free);
        initHardwareOriginal = reinterpret_cast<HardwareAction>(init);
        freeSlot = reinterpret_cast<mach_vm_address_t>(freeEngine);
        initSlot = reinterpret_cast<mach_vm_address_t>(initHardware);
        if (enable) {
            stopQueueStart = stop;
            stopQueueEnd = suspend;
            releaseHardwareOriginal = reinterpret_cast<HardwareAction>(release);
            releaseSlot = reinterpret_cast<mach_vm_address_t>(releaseHardware);
        }
        MachInfo::setKernelWriting(false, KernelPatcher::kernelWriteLock);
        engineVtable = vt;
        return enable ? MinOnMs : fallback;
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
    if (requested) {
        lilu.onKextLoadForce(&x6000);
        lilu.onKextLoadForce(&ioAccel);
    }
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
    bindEngine(engine);
    if (queueDrainRouted) {
        // kext 加载回调早于元类初始化，在分配引擎时校验对象大小。
        const auto enabled = engine && queueDrainLayoutFits();
        __atomic_store_n(&queueDrainEngine, enabled ? engine : nullptr, __ATOMIC_RELEASE);
    }
    if (!engineAllocated) { __atomic_store_n(&loadCompleted, false, __ATOMIC_RELEASE); }
    NRed::singleton().setProp32("NRedVCNEngineAllocated", engineAllocated);
    return engine;
}

void VCN::processKext(KernelPatcher& patcher, size_t id, mach_vm_address_t slide, size_t size)
{
    if (!supported()) { return; }
    if (id == ioAccel.loadIndex) {
        prepareQueueDrainWorkLoop(patcher, id, slide, size);
        return;
    }
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
        if (ok) { prepareQueueDrain5000(patcher, id, slide, size); }
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
        if (ok) {
            const auto minOn = installMinOnTime(patcher, id, slide, size, checkKernelArgument("-NRedNoVCNMinOn"));
            NRed::singleton().setProp32("NRedVCNMinOn", minOn);
        }
        has6000 = hooks = ok;
        NRed::singleton().setProp32("NRedVCNIdleGfxOffFix", ok);
        queueDrainRouted = ok && installQueueDrain(patcher, id, slide, size);
    }
    NRed::singleton().setProp32(old ? "NRedVCNX5000Prepared" : "NRedVCNX6000Prepared", ok);
    SYSLOG("VCN", "%s prepared=%u ready=%u", old ? "X5000" : "X6000", ok, ready());
}
