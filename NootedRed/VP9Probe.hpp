// Fase V1 del piano VP9 (-NRedVP9Probe): annuncia il VP9 a VideoToolbox e AppleGVA, senza backend.
// Pubblica sull'acceleratore le tre chiavi che servono (vedi docs/vp9-v0-findings.md):
//   IOGVAXDecode                  -> carica il plugin AppleGVAVPXDecoder (CMDependencies)
//   IOGVAVP9Decode                -> AppleGVA trova il codec VP9 della GPU
//   IOGVAVP9DecodeCapabilities    -> AppleGVA legge i profili (VTSupportedProfileArray)
// Il driver AMD non sa ancora decodificare il VP9: la sessione deve fallire in modo pulito alla creazione del
// decoder. Per questo la sonda resta spenta di default: con le chiavi pubblicate i browser sceglierebbero il VP9.
// Tutto o niente: se una chiave non si scrive, quelle già scritte vengono tolte.
#pragma once
#include <IOKit/IOTypes.h>
#include <stddef.h>

namespace VP9Probe
{

    constexpr const char* BootArg         = "-NRedVP9Probe";
    constexpr const char* StatusKey       = "NRedVP9Probe";
    constexpr const char* PluginKey       = "IOGVAXDecode";
    constexpr const char* PluginValue     = "2";    // stesso valore dei Mac Intel che hanno il VP9
    constexpr const char* DecodeKey       = "IOGVAVP9Decode";
    constexpr const char* DecodeValue     = "1";    // come IOGVAHEVCDecode nella personality
    constexpr const char* CapabilitiesKey = "IOGVAVP9DecodeCapabilities";
    constexpr const char* ProfilesKey     = "VTSupportedProfileArray";
    // Solo profilo 0 (8 bit 4:2:0). Il profilo 2 (10 bit) arriva con la fase V6.
    constexpr UInt32      Profiles[]      = {0};
    constexpr size_t      ProfileCount    = sizeof(Profiles) / sizeof(Profiles[0]);

    enum Status : UInt32
    {
        Failed      = 0,    // scrittura non riuscita, nessuna chiave lasciata
        Published   = 1,
        NotEligible = 2,    // ponte VCN non pronto o acceleratore non avviato
    };

    // Sink: bool string(key, value); bool profiles(key, arrayKey, values, count); void remove(key).
    template<typename Sink>
    Status publish(Sink& sink, bool eligible)
    {
        if (!eligible) { return NotEligible; }
        if (!sink.string(PluginKey, PluginValue)) { return Failed; }
        if (!sink.string(DecodeKey, DecodeValue)) {
            sink.remove(PluginKey);
            return Failed;
        }
        if (!sink.profiles(CapabilitiesKey, ProfilesKey, Profiles, ProfileCount)) {
            sink.remove(DecodeKey);
            sink.remove(PluginKey);
            return Failed;
        }
        return Published;
    }

}    // namespace VP9Probe
