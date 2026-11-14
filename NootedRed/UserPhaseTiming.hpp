// Fase 0: cronometro per fase dei patch del shared cache, attivo solo con -NRedPhaseTiming.
// Spento costa un solo controllo di flag per fase; acceso accumula somma, massimo e campioni
// in contatori atomici e pubblica in ioreg ai primi 4 campioni del coordinatore e poi ogni 64.
#pragma once
#include <IOKit/IOTypes.h>
#include <NRed.hpp>
#include <kern/clock.h>

namespace UserPhaseTiming
{

    enum class Phase : UInt32
    {
        CoordinateFull,       // chiamata coordinata con ricerca (lease + header + display + video)
        CoordinateSkipped,    // chiamata coordinata saltata dalla fase 1 (lease + header + proof)
        Header,               // lettura base + header nel coordinatore
        SkipCheck,            // ricerca della mappa completata + verifica delle proof
        Display,              // callback display intero
        DisplayResolve,       // ricerca Submit + Metal (con cache FVRC)
        DisplayPrepare,       // adattamento, lettura e verifica dei siti, SubmitGuard
        DisplayWrite,         // commit: COW, permessi, scrittura, rilettura, ripristino RX
        Video,                // callback video intero
        VideoResolve,         // ricerca video (con cache FVRC)
        VideoPrepare,         // piano di installazione, lettura dei siti, ledger
        VideoWrite,           // commit video
        Count
    };

    inline const char* const avgKeys[] = {
        "NRedPhaseCoordinateFullAvgUs", "NRedPhaseCoordinateSkippedAvgUs", "NRedPhaseHeaderAvgUs",
        "NRedPhaseSkipCheckAvgUs",      "NRedPhaseDisplayAvgUs",           "NRedPhaseDisplayResolveAvgUs",
        "NRedPhaseDisplayPrepareAvgUs", "NRedPhaseDisplayWriteAvgUs",      "NRedPhaseVideoAvgUs",
        "NRedPhaseVideoResolveAvgUs",   "NRedPhaseVideoPrepareAvgUs",      "NRedPhaseVideoWriteAvgUs",
    };
    inline const char* const maxKeys[] = {
        "NRedPhaseCoordinateFullMaxUs", "NRedPhaseCoordinateSkippedMaxUs", "NRedPhaseHeaderMaxUs",
        "NRedPhaseSkipCheckMaxUs",      "NRedPhaseDisplayMaxUs",           "NRedPhaseDisplayResolveMaxUs",
        "NRedPhaseDisplayPrepareMaxUs", "NRedPhaseDisplayWriteMaxUs",      "NRedPhaseVideoMaxUs",
        "NRedPhaseVideoResolveMaxUs",   "NRedPhaseVideoPrepareMaxUs",      "NRedPhaseVideoWriteMaxUs",
    };
    inline const char* const sampleKeys[] = {
        "NRedPhaseCoordinateFullSamples", "NRedPhaseCoordinateSkippedSamples", "NRedPhaseHeaderSamples",
        "NRedPhaseSkipCheckSamples",      "NRedPhaseDisplaySamples",           "NRedPhaseDisplayResolveSamples",
        "NRedPhaseDisplayPrepareSamples", "NRedPhaseDisplayWriteSamples",      "NRedPhaseVideoSamples",
        "NRedPhaseVideoResolveSamples",   "NRedPhaseVideoPrepareSamples",      "NRedPhaseVideoWriteSamples",
    };
    constexpr UInt32 PhaseCount = static_cast<UInt32>(Phase::Count);
    static_assert(sizeof(avgKeys) / sizeof(avgKeys[0]) == PhaseCount);
    static_assert(sizeof(maxKeys) / sizeof(maxKeys[0]) == PhaseCount);
    static_assert(sizeof(sampleKeys) / sizeof(sampleKeys[0]) == PhaseCount);

    struct Slot
    {
        UInt64 sum{0}, max{0};
        UInt32 samples{0};
    };

    // Stato unico definito in UserPatchCoordinator.cpp: nel kext (LTO, -fapple-kext) le variabili `inline`
    // di un header restano una copia per file .cpp, quindi il flag acceso in un modulo non si vedrebbe negli altri.
    UInt8& enabledFlag();
    Slot*  slots();
    UInt32& publishTicket();

    inline bool enabled() { return __atomic_load_n(&enabledFlag(), __ATOMIC_RELAXED) != 0; }

    inline void setEnabled(const bool value)
    {
        __atomic_store_n(&enabledFlag(), static_cast<UInt8>(value ? 1 : 0), __ATOMIC_RELEASE);
        NRed::singleton().setProp32("NRedPhaseTiming", value ? 1 : 0);
    }

    inline UInt64 now() { return enabled() ? mach_absolute_time() : 0; }

    inline UInt32 toMicroseconds(const UInt64 absolute)
    {
        UInt64 ns{0};
        absolutetime_to_nanoseconds(absolute, &ns);
        const auto us = ns / 1000;
        return us > 0xFFFFFFFFULL ? 0xFFFFFFFFU : static_cast<UInt32>(us);
    }

    // Pubblicazione fuori da ogni lock: valori letti in modo rilassato, solo diagnostica.
    inline void publishAll()
    {
        for (UInt32 i = 0; i < PhaseCount; ++i) {
            auto&      slot    = slots()[i];
            const auto samples = __atomic_load_n(&slot.samples, __ATOMIC_RELAXED);
            if (samples == 0) { continue; }
            const auto sum = __atomic_load_n(&slot.sum, __ATOMIC_RELAXED);
            NRed::singleton().setProp32(avgKeys[i], toMicroseconds(sum / samples));
            NRed::singleton().setProp32(maxKeys[i], toMicroseconds(__atomic_load_n(&slot.max, __ATOMIC_RELAXED)));
            NRed::singleton().setProp32(sampleKeys[i], samples);
        }
    }

    inline void record(const Phase phase, const UInt64 start)
    {
        if (start == 0 || !enabled()) { return; }
        const auto end = mach_absolute_time();
        if (end < start) { return; }
        const auto delta = end - start;
        auto&      slot  = slots()[static_cast<UInt32>(phase)];
        __atomic_fetch_add(&slot.sum, delta, __ATOMIC_RELAXED);
        auto observed = __atomic_load_n(&slot.max, __ATOMIC_RELAXED);
        while (delta > observed
               && !__atomic_compare_exchange_n(&slot.max, &observed, delta, false, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
        {
        }
        __atomic_fetch_add(&slot.samples, 1U, __ATOMIC_RELAXED);
        // Il coordinatore scandisce la pubblicazione: una chiamata coordinata termina sempre con uno dei due.
        if (phase == Phase::CoordinateFull || phase == Phase::CoordinateSkipped) {
            const auto ticket = __atomic_add_fetch(&publishTicket(), 1U, __ATOMIC_RELAXED);
            if (ticket <= 4 || (ticket & 63) == 0) { publishAll(); }
        }
    }

    // Misura uno scope; stop() chiude prima, altrimenti chiude il distruttore (anche sui return anticipati).
    class Timer
    {
        Phase  phase;
        UInt64 start;

    public:
        explicit Timer(const Phase phase) : phase(phase), start(now()) {}
        ~Timer() { stop(); }
        void stop()
        {
            record(phase, start);
            start = 0;
        }
        // Cambia la fase registrata, es. da CoordinateFull a CoordinateSkipped.
        void retarget(const Phase value) { phase = value; }
        Timer(const Timer&)            = delete;
        Timer& operator=(const Timer&) = delete;
    };

}    // namespace UserPhaseTiming
