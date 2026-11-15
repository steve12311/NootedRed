# Piano: decodifica VP9 in hardware sul VCN di Renoir

Obiettivo: far decodificare al VCN 2.2 di Renoir i video VP9 (YouTube, Chrome, Safari), oggi decodificati dalla CPU.
Riferimento hardware: Ryzen 5 4600H, Radeon Renoir `0x1636` rev `0xC7`, macOS 26.7.1 (Darwin 25).

È il lavoro più grande e rischioso fatto finora su NootedRed. Il piano è diviso in fasi brevi: ognuna ha un criterio
di riuscita e un **punto di stop**, perché alcune fasi possono dimostrare che la strada non è percorribile.

## Perché ne vale la pena

- YouTube nei browser usa soprattutto VP9: oggi un video 1080p/4K tiene occupata la CPU. Nelle prove dell'indagine
  video, la decodifica software di HEVC 4K costava 11–21 s di CPU contro 0,5 s in hardware: con VP9 il rapporto sarebbe
  simile, quindi meno consumi e meno calore.
- Chrome usa VideoToolbox per il VP9 quando `VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9)` è vero.
  Safari fa una scelta simile tramite WebKit. Quindi, se il VP9 hardware esiste per VideoToolbox, i browser lo usano
  senza modifiche.

## Cosa sappiamo già (verificato il 14/11/2026)

| Fatto | Come lo sappiamo |
| --- | --- |
| VideoToolbox oggi: H.264 e HEVC in hardware, **VP9 no** | `VTIsHardwareDecodeSupported` |
| Il VCN 2.2 di Renoir decodifica VP9 (profilo 0 e 2) | driver Linux `amdgpu` (codec di Renoir) e Mesa |
| Il firmware VCN caricato da NootedRed per Renoir è il VCN 2.2 (`ativvaxy_nv.dat`, versione `0x0202`) | `HWLibs.cpp`, intestazione del file |
| **AppleGVA ha già un decoder VP9 completo**: lettura degli header, superframe, tile, lista dei riferimenti | stringhe `Flow_VP9Decoder`, `VP9_rlm.cpp`, `[VP9] …` in `AppleGVA` |
| AppleGVA decide se usare il VP9 hardware leggendo `IOGVAVP9Decode` e `IOGVAVP9DecodeCapabilities` dalla GPU | stringhe in `AppleGVA`; oggi la GPU pubblica solo le chiavi H.264/HEVC (`com.apple.kext.AMDRadeonX5000.xml`) |
| AppleGVA passa il fotogramma VP9 al driver del produttore con il comando **`kAVDPictureDecodeVP9`** | gestito nei driver Intel (`AppleIntelKBLGraphicsVADriver`, `…ICL…`: "AVD error: %s kAVDPictureDecodeVP9") |
| **Il driver AMD (`AMDRadeonVADriver2`) non gestisce il VP9**: ha processori per AVC, HEVC, MPEG-2, VC-1, JPEG e AV1 (AV1 solo VCN3) | export di `AMDRadeonVADriver2` (`dyld_info -exports`) |
| Il driver AMD sa già costruire i comandi del VCN 2 (`Vcn2DecCommand`) e ha lo schema dei processori (`AvdVcnHevcDecDataProcessor`, `AvdVcnAvcDecDataProcessor`) | export di `AMDRadeonVADriver2` |
| VideoToolbox ha controlli per processo sul VP9 | export `VTIsVP9DecodeEnabledForCurrentProcess`, `kVTVideoDecoderSpecification_AllowVP9Decode` |

## Come funzionerebbe

```text
App (Chrome / Safari / ffmpeg)
  → VideoToolbox            controllo per processo sul VP9
  → AppleGVA                Flow_VP9Decoder: header, superframe, tile, riferimenti   (esiste già)
  → driver del produttore   kAVDPictureDecodeVP9                                     (MANCA nel driver AMD)
  → AMDRadeonVADriver2      messaggio di decodifica VCN 2 + buffer                   (da scrivere: backend VP9)
  → kernel                  AMDRadeonX6000_AMDVCNHWChannel::submitCommandBuffer      (esiste, già usato per HEVC)
  → firmware VCN 2.2        decodifica VP9                                           (esiste)
```

Il pezzo da scrivere è uno solo, ma è grande: il **backend VP9 dentro il driver AMD**. Deve tradurre i parametri del
fotogramma che arrivano da AppleGVA nel messaggio che il firmware VCN si aspetta. Come riferimento c'è Mesa (licenza MIT):

- `src/amd/common/ac_vcn_dec.h`: struttura `rvcn_dec_message_vp9_t` e intestazioni del messaggio;
- `src/gallium/drivers/radeonsi/radeon_vcn_dec.c`: `get_vp9_msg`, dimensioni dei buffer di contesto, tabella
  delle probabilità (`ac_vcn_vp9_fill_probs_table`), mappa di segmentazione, gestione dei riferimenti (8 slot);
- Linux `drivers/gpu/drm/amd/amdgpu/vcn_v2_0.c`: anelli e messaggi del VCN 2.

## Le tre incognite

1. **Il formato di `kAVDPictureDecodeVP9`.** Che struttura riceve il driver: parametri del fotogramma, riferimenti,
   buffer. I driver Intel la gestiscono, quindi si possono usare come esempio di lettura insieme ad AppleGVA, che la scrive.
2. **Dove far vivere il codice nuovo.** NootedRed oggi corregge pochi byte nelle librerie già caricate. Il backend VP9
   è codice nuovo, qualche decina di KB, in un processo di sistema con firma del codice. Strade da valutare, in ordine:
   - **A. Riusare lo spazio del processore AV1**: su Renoir (VCN 2) il codice AV1 del driver AMD non viene mai eseguito.
     Lo si sovrascrive con il backend VP9, compilato come blocco indipendente dalla posizione e collegato alle funzioni
     del driver tramite i simboli esportati. È lo stesso meccanismo di scrittura dei patch attuali, solo più grande.
   - **B. Una nuova area eseguibile nel processo**, creata dal kernel: più flessibile, ma scontra con la firma del codice.
   - **C. Un'estensione MediaExtension** (`MEVideoDecoder`, macOS 15+): non tocca i processi di sistema, ma la usano
     solo le app che la abilitano (non Safari) e deve parlare con la GPU da sandbox. Ultima scelta.
3. **I controlli di VideoToolbox sul VP9.** Se il VP9 hardware è limitato ad alcune app o ad alcuni modelli di Mac,
   bisogna capire quali condizioni servono prima di investire nel backend.

## Fasi

Regole di sempre: una fase alla volta, ognuna dietro boot-arg spento di default, backup dell'EFI sul Desktop prima
di ogni installazione, test offline verdi, commit separati. In più, per questo lavoro: **le prime prove di decodifica
solo con `ffmpeg`**, mai con il browser, finché i fotogrammi non sono identici a quelli di libvpx.

### Fase V0 — Studio, nessun rischio (solo lettura)

- **V0a. Il comando AVD:** valore di `kAVDPictureDecodeVP9` e layout dei parametri, dal lato di AppleGVA
  (`Flow_VP9Decoder`, chi chiama il driver) e dal lato dei driver Intel (chi li legge).
- **V0b. Il driver AMD:** come `VADecoder` sceglie il processore in base al codec, come `Vcn2DecCommand` costruisce
  il messaggio per HEVC (sarà lo stampo per VP9), come alloca i buffer di contesto e come restituisce errore per un
  codec che non conosce.
- **V0c. I controlli di VideoToolbox:** piccolo programma che prova `kVTVideoDecoderSpecification_AllowVP9Decode` e
  legge `VTIsVP9DecodeEnabledForCurrentProcess` / `VTCopyVP9DecoderCapabilitiesDictionary`; capire se dipendono da
  entitlement, modello del Mac o proprietà della GPU.
- **V0d. Spazio disponibile:** misurare quanto codice occupa il processore AV1 (e gli altri inutilizzati su VCN 2)
  e stimare la dimensione del backend VP9 compilato.

**Fatto quando:** un documento con il layout di `kAVDPictureDecodeVP9`, il punto di aggancio nel driver AMD, le regole
di VideoToolbox e la scelta tra A, B e C.
**Stop se:** VideoToolbox abilita il VP9 hardware solo per hardware Apple in modo non aggirabile senza patch rischiose
a VideoToolbox, oppure non c'è modo ragionevole di far eseguire codice nuovo nel processo del decoder.

### Fase V1 — Sonda: annunciare il VP9 (`-NRedVP9Probe`)

- Con il boot-arg, NootedRed pubblica `IOGVAVP9Decode` e `IOGVAVP9DecodeCapabilities` sull'acceleratore (all'avvio,
  non nella personality statica, così senza boot-arg non cambia nulla).
- Si osserva: `VTIsHardwareDecodeSupported(VP9)` diventa vero? Con `ffmpeg -hwaccel videotoolbox` su un file VP9,
  AppleGVA arriva fino al driver AMD con `kAVDPictureDecodeVP9`? Il driver risponde con un errore pulito?
- Un contatore (stesso stile di `-NRedVCNPowerTrace`) registra le chiamate VP9 che arrivano al driver.

**Fatto quando:** vediamo arrivare i fotogrammi VP9 al driver AMD e ne leggiamo i parametri.
**Rischio:** basso. Senza backend la decodifica fallisce; per questo il boot-arg resta spento fuori dalle prove,
altrimenti i browser sceglierebbero VP9 e non riuscirebbero a riprodurre.
**Stop se:** AppleGVA non usa il percorso VP9 nemmeno con le proprietà pubblicate.

### Fase V2 — Traduttore VP9 → messaggio VCN, provato offline

- Libreria C++ senza dipendenze dal kernel o dal driver: riceve i parametri del fotogramma (formato di V0a) e produce
  il messaggio VCN VP9 e la tabella delle probabilità, porting di `get_vp9_msg` e funzioni collegate di Mesa.
- Test sul Mac, senza GPU: per i vettori di prova di libvpx (`vp90-2-*.webm`, profilo 0) si confrontano i messaggi
  prodotti con quelli generati dal codice di Mesa sugli stessi header (fotogramma per fotogramma, byte per byte).

**Fatto quando:** messaggi identici a Mesa su tutti i vettori del profilo 0 scelti.
**Rischio:** nullo (niente tocca il sistema).

### Fase V3 — Integrazione nel driver AMD (`-NRedVP9`)

- Scrivere il backend con la strada scelta in V0 (prima scelta: spazio del processore AV1) e agganciarlo al punto
  in cui il driver AMD smista `kAVDPictureDecodeVP9`.
- Allocare i buffer (contesto, probabilità, segmentazione, riferimenti) con le funzioni del driver, come fa per HEVC.
- Verifiche prima di scrivere, come per i patch attuali: firma dei byte, ABI, versione di macOS; se qualcosa non torna
  non si installa nulla.

**Fatto quando:** il primo fotogramma chiave di un video 352×288 decodificato con `ffmpeg` è identico a libvpx
(`-f framemd5`).
**Rischio:** alto. Un messaggio sbagliato può bloccare il VCN (blocco della decodifica, reset della GPU, panic).
Prove solo con `ffmpeg`, una alla volta, backup dell'EFI pronto.

### Fase V4 — Conformità del profilo 0

- Tutti i vettori di prova di libvpx del profilo 0: fotogrammi intermedi, riferimenti, tile, segmentazione,
  filtri, dimensioni dispari, 1080p e 4K. Ogni fotogramma deve essere identico a libvpx.
- Prestazioni e CPU come nell'indagine video (fotogrammi al secondo, secondi di CPU), più decodifiche in parallelo.

**Fatto quando:** zero differenze sui vettori e 4K a 60 fps con CPU bassa.

### Fase V5 — Browser

- Chrome e Safari su YouTube (`chrome://media-internals` mostra il decoder usato): riproduzione, salti nel video,
  cambi di risoluzione, schede multiple, sospensione e risveglio.

**Fatto quando:** un'ora di YouTube senza errori, CPU più bassa e conferma del decoder hardware.

### Fase V6 — Profilo 2 (10 bit, HDR)

- Stessa sequenza per il VP9 a 10 bit (YouTube HDR): uscita `p010`, vettori di prova del profilo 2.

## Rischi principali

| Rischio | Mitigazione |
| --- | --- |
| VCN bloccato da un messaggio errato (reset GPU, panic) | V2 offline prima di tutto, poi solo `ffmpeg`; boot-arg; backup EFI |
| Video corrotto senza errori | confronto bit a bit con libvpx (`framemd5`) su ogni vettore |
| Browser che scelgono VP9 e non riescono a riprodurre | proprietà VP9 pubblicate solo con il boot-arg; mai attivo di default finché V5 non passa |
| Firma del codice nel processo del decoder | scelta della strada in V0, verificata prima di scrivere il backend |
| Aggiornamenti di macOS che cambiano driver e strutture | verifiche di firma/ABI come i patch esistenti; se non combaciano il VP9 resta spento |
| Licenze | Mesa è MIT: mantenere l'avviso nei file portati |

## Stima onesta

- V0 e V1: alcuni giorni, quasi solo lettura del codice di Apple.
- V2: una o due settimane (porting e test di confronto).
- V3: l'incognita maggiore; dipende dalla strada scelta in V0. Può richiedere settimane o rivelarsi non fattibile.
- V4–V6: settimane, soprattutto di prove.

Le fasi V0 e V1 dicono presto se il progetto si può fare: conviene fare quelle, poi decidere se proseguire.

## Stato

- **V0 — completata (14/11/2026).** Nessuna condizione di stop; scelta la strada A. Risultati in
  [VP9 V0](vp9-v0-findings.md): layout di `kAVDPictureDecodeVP9` (`kind 1`, `type 0x1000`), punti di aggancio nel
  driver AMD, i tre cancelli di VideoToolbox.
- **V1 — completata (14/11/2026).** Con `-NRedVP9Probe` le chiavi sono pubblicate, VideoToolbox trova il decoder
  VP9 hardware dopo la registrazione e AppleGVA arriva al driver AMD, che rifiuta il contesto (`-12907`, chiusura
  pulita): `AvdVAContext::GetVAFunctionType` non conosce `type 0x1000`. Dettagli e strumento di prova
  (`tools/probe_vp9_decode.m`, perché `ffmpeg` 6.1 non registra VP9) in [VP9 V0](vp9-v0-findings.md#v1--sonda-sulla-macchina-14112026-superata).
- **V2 — completata (14/11/2026).** `NootedRed/VP9Message.hpp` produce gli stessi messaggi VCN di Mesa, byte per
  byte, su 222 fotogrammi di 9 clip libvpx (profilo 0 e 2); 6,6 KB compilato per il kernel. Non tocca il kext, quindi
  nessun boot-arg. Dettagli e limiti della copertura in [VP9 V0](vp9-v0-findings.md#v2--messaggio-vcn-provato-offline-14112026-superata).
- **V3 — completata (14/11/2026).** Studio del driver concluso: percorso AV1 da riusare, layout del messaggio e dei buffer del
  comando, scelta del DPB Tier 1 come Mesa, cinque patch previsti dietro `-NRedVP9`.
  Progetto in [VP9 V0](vp9-v0-findings.md#v3--progetto-dellintegrazione-studio-del-driver-14112026).
  Backend, generatore delle patch e cablaggio nel kext dietro `-NRedVP9` scritti e provati offline (messaggi uguali a
  Mesa, resolver verde sulla cache reale). Dettagli in
  [VP9 V0](vp9-v0-findings.md#v3--backend-e-cablaggio-nel-kext-14112026-offline-verde-non-ancora-provato-sulla-macchina).
  Prima prova sulla macchina: AppleGVA non crea il contesto perché il driver non dichiara una capability VP9.
  Ora la funzione 6 (AV1) viene dichiarata come VP9. Con il filtro sulla maschera hardware e il bitstream diretto,
  la decodifica in processo (lldb) dà luma identica a ffmpeg su tutte le clip a 8 bit. Col kext installato (decoder fuori
  processo) il risultato è lo stesso. Il profilo 2 a 10 bit
  ora viene rifiutato alla creazione della sessione (provato anche col kext installato). Anche la crominanza e
  quattro clip 1080p sono identiche bit per bit a ffmpeg, così come il 4K (fino a 4096x2160) e clip lunghe (2 minuti a
  1080p, 5 minuti a 720p). Gli stream oltre i 4096 pixel e i fotogrammi che il driver non decodifica ora falliscono
  in modo esplicito invece di uscire corrotti. Dettagli in
  [VP9 V0](vp9-v0-findings.md#v3--prima-prova-sulla-macchina-e-gate-delle-capability-14112026) e
  [decodifica in processo](vp9-v0-findings.md#v3--decodifica-in-processo-e-primi-fotogrammi-corretti-14112026).
- **V4 — in corso.** Prestazioni misurate col kext installato: 4K60 decodificato a 150 fps con circa 0,9 s di CPU
  ogni 10 s di video, contro 14–18 s con libvpx o il decoder di ffmpeg. Più sessioni in parallelo si dividono la VCN in
  parti uguali e restano identiche a ffmpeg, anche insieme a decodifiche HEVC e H.264 in hardware. Manca la
  conformità sui vettori di prova di libvpx, che vanno scaricati. Dettagli in
  [VP9 V0](vp9-v0-findings.md#v4--prestazioni-e-decodifiche-in-parallelo-14112026).
- **V5 — in corso.** Prima prova di 5,5 minuti: YouTube in Chrome (4K e 1440p, attraverso `VTDecoderXPCService`) e in
  Safari (1080p e 4K, dentro `com.apple.WebKit.GPU`) usa il decoder VP9 hardware. Immagine corretta, nessun errore di
  decodifica, nessun problema del kernel. Mancano l'ora di riproduzione, le schede multiple e la sospensione. Dettagli in
  [VP9 V0](vp9-v0-findings.md#v5--prima-prova-nei-browser-14112026).
