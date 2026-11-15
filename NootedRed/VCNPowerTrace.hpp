// Fase 5, passo A: osserva lo spegnimento del VCN fatto da PowerPlay di Apple, senza cambiarlo.
// Attivo solo con -NRedVCNPowerTrace. Avvolge `PHM_PowerGateVCN(hwmgr, gate, instance)`, che `PSM_ReArbit`
// chiama con gate=1 quando non c'è attività video e gate=0 quando serve il VCN. L'originale viene sempre
// chiamato con gli stessi argomenti e il suo risultato restituito invariato.
//
// Il wrapper aggiorna solo contatori atomici: niente lock, allocazioni o ioreg dentro PowerPlay, che può
// girare con lock propri. La pubblicazione avviene dopo, da un contesto thread normale (`publishPending`).
#pragma once
#include <IOKit/IOTypes.h>

namespace VCNPowerTrace
{

    using GateFn  = UInt32 (*)(void* hwmgr, UInt32 gate, UInt32 instance);
    using Publish = void (*)(const char* key, UInt32 value);

    // Layout verificato su macOS 26 (AMDRadeonX5000HWLibs): hwmgr+0x1328 è il backend di PowerGateVCN;
    // per `PhwRenoir_PowerGateVCN` lo stato "spento" è in *(hwmgr+0xA0) + 0x3D0.
    // Lo stato si legge solo se il backend coincide con l'indirizzo risolto di `PhwRenoir_PowerGateVCN`:
    // se il layout cambiasse il confronto fallisce e non si dereferenzia altro.
    constexpr UInt32 BackendOffset     = 0x1328;
    constexpr UInt32 BackendDataOffset = 0xA0;
    constexpr UInt32 RenoirGatedOffset = 0x3D0;
    constexpr UInt32 ResultOK          = 1;
    constexpr UInt32 Unknown           = 0xFFFFFFFF;

    enum class Backend : UInt32
    {
        Unknown = 0,
        None    = 1,    // nessun backend: PHM risponde OK senza fare nulla
        Renoir  = 2,    // PhwRenoir_PowerGateVCN (messaggi SMU 0xB/0xC)
        SwSmu   = 3,    // Phw_SwSmu_PowerGateVcn
        Other   = 4,
    };

    struct State
    {
        GateFn original{nullptr};
        void*  renoirBackend{nullptr};
        void*  swSmuBackend{nullptr};
        bool   layoutVerified{false};
        UInt32 gateCalls{0}, ungateCalls{0}, failures{0};
        UInt32 lastResult{Unknown}, lastGate{Unknown}, backend{0}, gated{Unknown};
        UInt32 version{0}, published{0};
    };

    inline Backend classify(const State& state, void* const hwmgr)
    {
        if (hwmgr == nullptr) { return Backend::Unknown; }
        const auto fn = *reinterpret_cast<void* const*>(static_cast<const UInt8*>(hwmgr) + BackendOffset);
        if (fn == nullptr) { return Backend::None; }
        if (state.renoirBackend && fn == state.renoirBackend) { return Backend::Renoir; }
        if (state.swSmuBackend && fn == state.swSmuBackend) { return Backend::SwSmu; }
        return Backend::Other;
    }

    // 1 = spento, 0 = acceso.
    inline UInt32 renoirGated(void* const hwmgr)
    {
        const auto data = *reinterpret_cast<const UInt8* const*>(static_cast<const UInt8*>(hwmgr) + BackendDataOffset);
        if (data == nullptr) { return Unknown; }
        return *reinterpret_cast<const UInt32*>(data + RenoirGatedOffset) != 0 ? 1 : 0;
    }

    inline UInt32 call(State& state, void* const hwmgr, const UInt32 gate, const UInt32 instance)
    {
        const auto result  = state.original(hwmgr, gate, instance);
        const auto backend = state.layoutVerified ? classify(state, hwmgr) : Backend::Unknown;
        const auto gated   = backend == Backend::Renoir ? renoirGated(hwmgr) : Unknown;
        __atomic_add_fetch(gate != 0 ? &state.gateCalls : &state.ungateCalls, 1U, __ATOMIC_RELAXED);
        if (result != ResultOK) { __atomic_add_fetch(&state.failures, 1U, __ATOMIC_RELAXED); }
        __atomic_store_n(&state.lastResult, result, __ATOMIC_RELAXED);
        __atomic_store_n(&state.lastGate, gate, __ATOMIC_RELAXED);
        __atomic_store_n(&state.backend, static_cast<UInt32>(backend), __ATOMIC_RELAXED);
        __atomic_store_n(&state.gated, gated, __ATOMIC_RELAXED);
        __atomic_add_fetch(&state.version, 1U, __ATOMIC_RELEASE);
        return result;
    }

    // Da chiamare in contesto thread (es. coordinatore dei patch userspace). Pubblica solo se qualcosa è cambiato.
    inline void publish(State& state, const Publish out)
    {
        const auto version = __atomic_load_n(&state.version, __ATOMIC_ACQUIRE);
        auto       seen    = __atomic_load_n(&state.published, __ATOMIC_RELAXED);
        if (version == seen
            || !__atomic_compare_exchange_n(&state.published, &seen, version, false, __ATOMIC_RELAXED,
                                            __ATOMIC_RELAXED))
        {
            return;
        }
        out("NRedVCNPGGateCalls", __atomic_load_n(&state.gateCalls, __ATOMIC_RELAXED));
        out("NRedVCNPGUngateCalls", __atomic_load_n(&state.ungateCalls, __ATOMIC_RELAXED));
        out("NRedVCNPGFailures", __atomic_load_n(&state.failures, __ATOMIC_RELAXED));
        out("NRedVCNPGLastGate", __atomic_load_n(&state.lastGate, __ATOMIC_RELAXED));
        out("NRedVCNPGLastResult", __atomic_load_n(&state.lastResult, __ATOMIC_RELAXED));
        out("NRedVCNPGBackend", __atomic_load_n(&state.backend, __ATOMIC_RELAXED));
        out("NRedVCNPGGated", __atomic_load_n(&state.gated, __ATOMIC_RELAXED));
    }

    // Definite in HWLibs.cpp (stato unico: le variabili inline degli header sono una copia per file nel kext).
    void publishPending();

}    // namespace VCNPowerTrace
