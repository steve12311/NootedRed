# VP9 in hardware — risultati della fase V0

Fase V0 del [piano VP9](vp9-hw-decode-plan.md): solo lettura, nessuna modifica al sistema.
Sistema: macOS 26.7.1 (Darwin 25), cache dyld `x86_64h`, Ryzen 5 4600H (Renoir `0x1636`). Data: 14/11/2026.

**Esito: si prosegue.** Nessuna condizione di stop: VideoToolbox non limita il VP9 hardware ai Mac Apple, e il codice
nuovo può vivere nello spazio del processore AV1 del driver AMD (strada A), con lo stesso meccanismo dei patch attuali.

Strumenti usati: `dyld_info`, `objdump` (driver Intel su disco), un piccolo disassemblatore della cache dyld basato su
Capstone (solo per lo studio, fuori dal repository) e [`tools/probe_vp9_videotoolbox.m`](../tools/probe_vp9_videotoolbox.m).

## V0c — I controlli di VideoToolbox (punto di stop: superato)

Il VP9 hardware passa per tre cancelli, tutti soddisfacibili senza toccare VideoToolbox:

| Cancello | Dove | Cosa chiede | Oggi su Renoir |
| --- | --- | --- | --- |
| 1. Plugin | `AppleGVAVPXDecoder.bundle`, `CMDependencies` | chiave `IOGVAXDecode` nel registro I/O | manca |
| 2. AppleGVA | funzione che cerca il codec della GPU | `IOGVAVP9Decode` e `IOGVAVP9DecodeCapabilities` (con `VTSupportedProfileArray`) sul nodo della GPU, ricerca ricorsiva come per HEVC | mancano |
| 3. Processo | `VTRegisterSupplementalVideoDecoderIfAvailable('vp09')` | l'app chiede il VP9; il client aggiunge `kVTVideoDecoderSpecification_AllowVP9Decode` alla sessione | lo fanno Chrome e Safari (WebKit) |

Dettagli verificati nel codice:

- `VTRegisterSupplementalVideoDecoderIfAvailable` scrive solo un flag (`'vp09'` → 1). `VTIsVP9DecodeEnabledForCurrentProcess`
  legge quel flag. Alla creazione della sessione, se il flag è attivo, VideoToolbox aggiunge `AllowVP9Decode = true`;
  senza flag né chiave nella specifica la sessione `vp09` viene rifiutata. Il servizio di decodifica remoto
  (`VTRemoteDecodeServer`) accetta sempre il VP9.
- `AppleGVAVPXDecoder` (48 KB) non importa IOKit: carica AppleGVA e chiama le sue funzioni. Nessun controllo di
  produttore o modello.
- Il plist dei modelli di AppleGVA (`Resources/Info.plist`, sezione `System Support`) non ha chiavi VP9: regola altre
  funzioni (`hwe`, `ke1`, `pp`, …).
- I Mac Intel (`AppleIntelKBLGraphics`, `AppleIntelICLGraphics`) pubblicano `IOGVAXDecode = "2"` nella personality
  dell'acceleratore.
- Prova attuale (`probe_vp9_videotoolbox`): `VTIsHardwareDecodeSupported(vp09) = 0` prima e dopo la registrazione,
  creazione della sessione `-12906` (nessun decoder) in tutti i casi. Atteso: manca il cancello 1.

Conseguenza per i browser: appena le chiavi sono pubblicate, Chrome e Safari sceglieranno il VP9 hardware. Per questo
le chiavi restano dietro un boot-arg spento finché la fase V5 non passa.

## V0a — Il comando `kAVDPictureDecodeVP9`

Ricavato dal driver Intel `AppleIntelKBLGraphicsVADriver` (su disco, compilato senza ottimizzazioni: facile da
leggere), che gestisce già il comando.

### Il comando

`AVD_CmdInfo` è una lista di blocchi. Ogni blocco: `u32 kind` a `+0x00`, `u64 type` a `+0x08`, puntatore ai dati a
`+0x20`, blocco successivo a `+0x30`.

- Decodifica: `kind = 1`. Tipi visti: `0x800` HEVC, **`0x1000` VP9**, `0x1000000` AV1 (solo AMD).
- Il driver Intel cerca il blocco `kind 1, type 0x1000` e legge la struttura dei parametri VP9 dal suo puntatore dati.
- Creazione della sessione VP9 (Intel): larghezza `+0x20`, altezza `+0x24` (massimo `0x2000`), formato `+0x30`
  (`'x420'` / `'xf20'` = 10 bit).

### La struttura dei parametri (`P`)

| Offset | Contenuto |
| --- | --- |
| `P+0x0000` | indirizzo del bitstream (il driver sottrae la base del buffer) |
| `P+0x0008` | dimensione del bitstream (`BSBytesInBuffer`) |
| `P+0x0010` | header VP9 (`H`, tabella sotto), fino a `P+0x1248` |
| `P+0x1248` | lunghezza dell'header non compresso in byte |
| `P+0x1250` | superficie di uscita (`IOSurfaceRef`) |
| `P+0x1260 + i·0x1258` | 8 riferimenti (`ref_frame_map[i]`), stessa forma del fotogramma corrente: superficie a `+0x1250` |
| `P+0xA520/24/28` | `ref_frame_idx[3]` (LAST, GOLDEN, ALTREF), 32 bit ciascuno |
| `P+0xA530`, `P+0xA540` | dati DRM e flag (bit 0 = cifrato) |

Header `H = P+0x10` (campi usati dal driver Intel per riempire `CODEC_VP9_PIC_PARAMS` di intel/media-driver):

| Offset `H` | Campo VP9 |
| --- | --- |
| `+0x02` (u16) | `first_partition_size` (header compresso) |
| `+0x05` | `frame_type` (0 = chiave) |
| `+0x06` | `show_frame` |
| `+0x07` | `error_resilient_mode` |
| `+0x08` | `intra_only` |
| `+0x0B`, `+0x0C` | `subsampling_x`, `subsampling_y` |
| `+0x0D` | `reset_frame_context` (2 bit) |
| `+0x11..0x14` | `ref_frame_sign_bias[0..3]` |
| `+0x15` | `allow_high_precision_mv` |
| `+0x16` | `interp_filter` (3 bit) |
| `+0x17` | `refresh_frame_context` |
| `+0x18` | `frame_parallel_decoding_mode` |
| `+0x19` | `frame_context_idx` |
| `+0x1A`, `+0x1B` | `filter_level`, `sharpness_level` |
| `+0x1C` | `mode_ref_delta_enabled` |
| `+0x22..0x25` | `ref_deltas[4]` (int8) |
| `+0x28..0x29` | `mode_deltas[2]` (int8) |
| `+0x2A` | `base_q_idx` |
| `+0x2B`, `+0x2C`, `+0x2D` | `delta_q_y_dc`, `delta_q_uv_dc`, `delta_q_uv_ac` (int8) |
| `+0x2E`, `+0x2F` | `segmentation_enabled`, `segmentation_update_map` |
| `+0x30..0x36` | `segmentation_tree_probs[7]` |
| `+0x37` | `segmentation_temporal_update` |
| `+0x38..0x3A` | `segmentation_pred_probs[3]` |
| `+0x3C` | `segmentation_abs_or_delta_update` |
| `+0x40`, `+0x44` (u32) | `log2_tile_cols`, `log2_tile_rows` |
| `+0x48`, `+0x49` | profilo, bit depth (8 / 10) |
| `+0x4C`, `+0x50` (u32) | larghezza, altezza |
| `+0xAD + 4·s + f` | `feature_enabled[8][4]` |
| `+0xCE + 8·s + 2·f` (int16) | `feature_data[8][4]` |

Il resto dell'header (fino a `0x1238` byte) non è letto dal driver Intel: probabile header compresso già analizzato da
AppleGVA. Il VCN non ne ha bisogno: legge da sé l'header compresso nel bitstream, come con VA-API in Mesa
(`uncompressed_header_size` e `compressed_header_size` bastano).

Da confermare in V1 con un fotogramma reale: il significato esatto di `interp_filter` (ordine del bitstream o ordine
del codec), il contenuto dei byte `+0x1D..0x21` e `+0x26..0x27` (probabilmente i flag `update_*_delta`), se il
bitstream a `P+0x0000` comincia dall'header non compresso.

## V0b — Il driver AMD (`AMDRadeonVADriver2`)

Percorso di un fotogramma e punti in cui il VP9 oggi si perde:

1. **Creazione**: `AvdVAContext::CreateDecoder(codec, …)` → `VAFactory::createDecoder(codec, ctx)`, `switch` su
   `codec` 0–6. Mappa vista: 0 → H.264 (VCN se il bit 16 delle capacità è attivo), 5 → HEVC (VCN), 6 → AV1 (sempre
   VCN, tipo 6); 1–4 → decoder UVD. **Un codec fuori da 0–6 restituisce `NULL`**: per il VP9 la sessione fallisce
   qui, in modo pulito. Il numero che AppleGVA usa per il VP9 si legge in V1.
2. **Processore**: una seconda `switch` crea il processore per tipo; tipo 6 → `AvdVcnAv1DecDataProcessor`.
3. **Comando**: `AvdVAContext::ScheduleCommand` manda ogni `kind 1` a `AvdVAContext::DecodePicture`, che gestisce i
   tipi `2, 8, 0x10, 0x40, 0x400, 0x800, 0x1000000`. **Per `0x1000` esce con 0 senza fare nulla.**
4. **Messaggio VCN**: `Vcn2DecCommand` (`writeUvdGpcomVcpuData0/1`, `writeUvdGpcomVcpuCmd`, `writeUvdEngineStart`)
   e i metodi del processore (`SetDecodeMessageFields`, `SetBitstreamFields`, `CalculateDPBSize`,
   `CalculateContextSize`, `initHWContext`). È lo schema da seguire.

Punti di aggancio per il VP9 (strada A):

- `VAFactory::createDecoder`: codec VP9 → decoder VCN di tipo 6.
- `AvdVAContext::DecodePicture`: tipo `0x1000` instradato come `0x1000000`, con superficie di uscita a `P+0x1250`.
- Metodi di `AvdVcnAv1DecDataProcessor` riscritti con il backend VP9 **agli stessi indirizzi**: la tabella virtuale
  resta valida e non serve nuovo spazio dati.

## V0d — Spazio disponibile

Codice AV1 contiguo in `AMDRadeonVADriver2` (offset dall'inizio dell'immagine), mai eseguito su VCN 2:

| Funzione | Byte |
| --- | --- |
| `SetDecodeMessageFields` | 3840 |
| `initModeProbs` | 2118 |
| `av1SdbInitGrainBlock` | 2102 |
| `av1InitMvProbs` + `av1DefaultCoefProbs` | 1168 |
| altri 18 metodi | 1188 |
| **Totale `0x427C0`–`0x45070`** | **10 416** |

Stima del backend VP9 (porting di `get_vp9_msg` e funzioni collegate di Mesa, compilato `-Os`): 3–5 KB di codice
più ~2,3 KB di tabelle di probabilità predefinite. Ci sta. Altro spazio, se servisse: i processori solo UVD
(`AvdAvcDecDataProcessor`, `AvdHevcDecDataProcessor`, …), mai usati su Renoir, e le tabelle AV1 in `__const`.

## Scelta della strada

**A — riusare lo spazio del processore AV1.** NootedRed già sostituisce funzioni intere in `AMDRadeonVADriver2`
(generatore `tools/build_video_decode_patch.py`, verifica dell'hash di ogni funzione, pagine COW private del
processo). Il backend VP9 sarà un altro gruppo di funzioni della stessa forma, solo più grande. B e C non servono.

## V1 — Sonda sulla macchina (14/11/2026: superata)

Con `-NRedVP9Probe` (insieme a `-NRedVideoFix`) NootedRed pubblica `IOGVAXDecode`, `IOGVAVP9Decode` e
`IOGVAVP9DecodeCapabilities` (profilo 0) sull'acceleratore, solo se il ponte VCN è pronto
([`VP9Probe.hpp`](../NootedRed/VP9Probe.hpp), hook `acceleratorStart` in [`VCN.cpp`](../NootedRed/VCN.cpp)).
Per tornare indietro basta togliere `-NRedVP9Probe`: senza boot-arg non cambia nulla.

| Prova | Prima della sonda | Con la sonda |
| --- | --- | --- |
| `ioreg` | nessuna chiave | `NRedVP9Probe = 1`, `IOGVAXDecode = "2"`, `IOGVAVP9Decode = "1"`, profili `(0)` |
| `VTIsHardwareDecodeSupported(vp09)` | 0 | 0 prima di `VTRegisterSupplementalVideoDecoderIfAvailable`, 1 dopo |
| sessione VT con `vpcC` ([`probe_vp9_decode`](../tools/probe_vp9_decode.m)) | `-12906` | `-12907` (`kVTCouldNotCreateInstance`), chiusura pulita |
| `ffmpeg 6.1.1 -hwaccel videotoolbox` | `Failed setup for format videotoolbox_vld` | `decoder for this format not found`, 60 fotogrammi in software |

Nessun blocco, nessun panic. Il registro di sistema mostra la catena completa fino al driver AMD:
`AppleGVAVPXDecoder` legge `biDepthLuma 8`, AppleGVA apre il contesto VP9 (`[VP9] burstSizeNormal: 3`), poi
`[VP9] GVA error: failed to create context` e `Couldn't set the stream resolution`.

**Perché fallisce (punto 1 di V0b chiuso).** AppleGVA non sceglie il numero di codec: passa il blocco AVD
(`kind 1`, `type 0x1000`) e il driver AMD lo traduce in `AvdVAContext::GetVAFunctionType`
(`0x7ffb10b5bc14` in questa cache). Per `kind 1` la tabella conosce `0x10 → 1`, `0x2 → 2`, `0x400 → 4`,
`0x800 → 5` (HEVC), `0x1000000 → 6` (AV1), e altri; `0x1000` (VP9) non c'è e cade su `0x13`. `CreateContext`
(`0x7ffb10b5ba7f`) fa lo switch su `0..0x12`, quindi `0x13` restituisce `NULL` → `-12907`. (`kind 0x20`, encode,
mappa `0x1000 → 0xe`: non riguarda la decodifica.)

Conseguenze per V3: il backend deve aggiungere `type 0x1000 → codec VP9` in `GetVAFunctionType`, accettarlo in
`CreateContext`/`VAFactory::createDecoder` (tipo decoder VCN 6, processore nello spazio AV1) e in
`AvdVAContext::DecodePicture`, che oggi per `0x1000` ritorna 0 senza fare nulla.

**Strumenti di prova.** `ffmpeg 6.1` non registra VP9 presso VideoToolbox e non arriva mai al driver: per V2/V3 si
usa [`probe_vp9_decode`](../tools/probe_vp9_decode.m) (`probe_vp9_decode clip.ivf [maxFrames]`; registra VP9 come i
browser, richiede l'hardware, conta fotogrammi buoni e calcola un digest della luma da confrontare con la decodifica
software). Il clip di prova si ottiene con `ffmpeg -i clip.webm -c copy -f ivf clip.ivf`. Il debugger non si
aggancia a `VTDecoderXPCService` con SIP attivo: le prove restano statiche più il registro di sistema.

**Niente browser con la sonda attiva:** Safari/Chrome vedrebbero VP9 hardware disponibile e fallirebbero.

## V2 — Messaggio VCN, provato offline (14/11/2026: superata)

[`VP9Message.hpp`](../NootedRed/VP9Message.hpp) (con le tabelle in [`VP9DefaultProbs.hpp`](../NootedRed/VP9DefaultProbs.hpp))
trasforma un fotogramma VP9 nel messaggio di decodifica del VCN 2: `rvcn_dec_message_vp9_t` (120 byte), la parte
segmentazione del buffer IT (`rvcn_dec_vp9_probs_segment_t`), le probabilità iniziali del contesto e la sua dimensione.
È il porting di Mesa `767fbf7c` (MIT): frontend VA `picture_vp9.c`, `si_dec_vp9`, `build_vp9_msg`,
`ac_vcn_vp9_fill_probs_table`, `calc_ctx_size_vp9`. Nessun legame con kernel o driver; il kext non lo include ancora.

**Ingresso scelto.** Solo i byte del fotogramma (già separato dal superframe), la superficie di uscita e le otto
superfici `ref_frame_map`. L'header non compresso si legge dal bitstream, come fa Mesa: non servono gli offset
dell'header `H` di AppleGVA ancora da confermare (`interp_filter`, `+0x1D..0x21`). In V3 restano da confermare solo
l'indirizzo e la dimensione del fotogramma a `P+0x0000/0x0008` e le superfici a `P+0x1250` / `P+0x1260 + i·0x1258`.

**Il test** ([`test_vp9_message.py`](../tools/test_vp9_message.py)) compila il codice vero di Mesa
([`fetch_vp9_mesa.sh`](../tools/fetch_vp9_mesa.sh), commit fissato) e lo alimenta come farebbe `ffmpeg` con VA-API:
l'header letto dal parser CBS di `ffmpeg` (indipendente dal nostro) diventa i parametri VA, poi il frontend VA di
Mesa, `si_dec_vp9` e `build_vp9_msg` producono il messaggio. Il confronto è byte per byte, fotogramma per fotogramma,
su messaggio, buffer di segmentazione, slot dei riferimenti e dimensioni degli header. Risultato: **222 fotogrammi
identici** su 9 clip libvpx (352×288 – 1280×720, dimensioni dispari, 2–4 colonne di tile, segmentazione `aq-mode`
1/2/3 con aggiornamento temporale, alt-ref nascosti a due passate, error resilient, lossless, profilo 2 a 10 bit sia
P010 sia con dithering verso NV12). In più: probabilità iniziali e dimensione del contesto uguali a Mesa, 20 000
fotogrammi casuali o troncati senza letture fuori dal buffer (ASan/UBSan), `show_existing_frame` e profili 1/3
rifiutati. Quattro mutazioni volute del codice (livelli del filtro, slot di riserva, `use_prev_frame_mvs`, dati di
segmentazione) vengono tutte rilevate.

**Non coperto dai clip** (libvpx via `ffmpeg` non li genera): fotogrammi `intra_only`, `show_existing_frame` in un
flusso reale, cambi di risoluzione con riferimenti scalati, più righe di tile, `sharpness`, segmenti con riferimento
forzato. I vettori ufficiali `vp90-2-*` di libvpx li coprirebbero (da scaricare: non fatto).

**Scelte da tenere presenti in V3.**

- Il test ha trovato un errore che a occhio non si vedeva: `RDECODE_DITHERING_RANDOM` vale 5, non 1.
- Come Mesa, la lunghezza dell'header non compresso passa da un campo a 8 bit; le probabilità di segmentazione
  seguono la specifica (255 se non codificate, quelle di predizione a 255 senza aggiornamento temporale).
- Dimensione compilata per il kernel (`-Os -mkernel`): 4,5 KB di codice + 2,1 KB di tabelle = 6,6 KB, entro i
  10 416 byte del processore AV1; restano ~3,8 KB per il collegamento al driver. Per starci il lettore di bit è
  fuori linea (senza: 8,3 KB di codice).
- Il resto del comando (messaggio di decodifica generico, buffer DPB/contesto/bitstream, registri) resta al codice
  `Vcn2DecCommand` che il driver AMD usa già per HEVC.

## V3 — Progetto dell'integrazione (studio del driver, 14/11/2026)

Indirizzi come offset nell'immagine di `AMDRadeonVADriver2` della cache attuale (UUID `31374756…`, base
`0x7ffb10afe000`). Il generatore li ritrova per hash, come gli altri patch: gli offset servono solo da riferimento.

### Percorso di un fotogramma AV1 (quello che riusiamo)

1. `AvdVAContext::GetVAFunctionType` (`0x5DC14`): `kind 1` → tipo funzione. `0x1000000` (AV1) → 6; ogni altro tipo
   sconosciuto → `0x13` (rifiuto). Il confronto è un solo `cmp rax, 0x1000000` a `0x5DD8D`.
2. `AvdVAContext::CreateContext` (`0x5DA7E`): tipi 0–6 → `CreateDecoder(tipo, w = info+0x20, h = info+0x24, …)`.
   `VAFactory::createDecoder`: tipo 6 → `VAVcnDecoder` con `[dec+0x78] = 6`; `VAVcnDecoder::Init` mette 7 nella
   sessione (`+0x58`, struttura di `0x2B8` byte, campo `+4`).
3. `ProcessVcnCreateDecodeSession` (`0x15E94`): il codec del messaggio di creazione si legge da una tabella sul
   campo `+4`; l'indice 7 scrive `0x13` (AV1) con `mov dword [rax], 0x13` a `0x15F77`. Buffer del messaggio per il
   tipo 6: `0x1008` byte (tabella a `0x87FA0`).
4. `VAFactory::createDecodeDataProcessor`: tipo 6 → `operator new(0x48)` (`mov edi, 0x48` a `0x0DE9A`), poi il
   costruttore di `AvdVcnAv1DecDataProcessor` (`0x427F2`).
5. `AvdVAContext::DecodePicture` (`0x5EAC2`), ramo AV1 a `0x5ED3A`: `decoder = cmd+0x18`, `pic = cmd+0x20`; il
   bitstream è la texture `[pool + idx·8 + 0x20]` con `idx = [[pic+0x60]]`, l'uscita viene da
   `getIOSurfaceBuffer(ctx+8, pic+0x18, …)`; poi `VADecoder::DecodePicture(pic, bs, out)` (vtable `+0x48`) e
   `setRefCon`.
6. `VADecoder::DecodePicture` (`0x892C`): `processor->Process` (vtable `+0x20`), poi alloca DPB (`sessione+0x38`,
   dimensione `+0x30` da `CalculateDPBSize`) e contesto (`sessione+0x1D0`, dimensione `+0x1C8` da
   `CalculateContextSize`), chiama `initHWContext` (vtable `+0x60`) per il tipo 6, costruisce e invia il comando.

### Metodi del processore AV1 (vtable `+0x10 + 8·n`)

| Slot | Metodo | Offset | Uso per il VP9 |
| --- | --- | --- | --- |
| `+0x20` | `Process` | `0x428AC` | invariato: chiama i tre metodi sotto, `build[0] = 2`, contatore `+0x20` |
| `+0x40` | `CalculateDPBSize` | `0x4292A` | DPB VP9 (sotto) |
| `+0x48` | `CalculateContextSize` | `0x42992` | `VP9Message::contextSize` (10 857 728 / +65 536 a 10 bit) |
| `+0x58` | `getDecodeTargetArea` | `0x45040` | larghezza/altezza del fotogramma VP9 |
| `+0x60` | `initHWContext` | `0x4428C` | probabilità predefinite VP9 nel contesto (`fillDefaultProbs`) |
| `+0x68` | `SetBitstreamFields` | `0x4299E` | copia `P+0` / `P+8` nel buffer del bitstream |
| `+0x70` | `SetDecodeMessageFields` | `0x42A80` (3840 B) | messaggio VP9 (sotto) |

Gli altri slot (`ProcessDecodeStatus`, `needDecodeAndCopy`, `SetDecodeFeedbackFields`, …) sono della classe base e
restano.

### Il messaggio che il driver costruisce (HEVC e AV1 a confronto)

Stesso schema di Mesa (`ac_vcn_dec.c`). Buffer del messaggio `sessione+0xE0`:

- header: `0x28`, dimensione totale e numero di buffer da `proc+0x34/+0x38`, `msg_type 1`, feedback = `proc+0x20`;
- indice: `DECODE (2, 0xBC)`, codec (`0x0D` HEVC `0x118`, `0x11` AV1 `0xDF8`), `DRM (3, 0x44)`, `0x0F` (`0xA8`);
- `rvcn_dec_message_decode_t`: `stream_type` (`0x10` HEVC, `0x13` AV1), dimensioni `dec+0x7C/+0x80`, `bsd_size =
  sessione+0x2B0`, `dpb_size = sessione+0x30`, `dt_size = pitch·h·3/2`, `hw_ctxt_size = sessione+0x1C8`, pitch
  allineati a 64, `dt_pitch/uv_pitch` dalla texture d'uscita, offset del croma `+0x88..+0x94`.

`_uvd_command_build_info` (gli indirizzi del comando):

| Campo | Buffer | AV1 |
| --- | --- | --- |
| `+0x04/+0x08` | messaggio | `sessione + 0x98 + 8·[sessione+0xD8]` |
| `+0x10/+0x18` | DPB | `sessione+0x38` |
| `+0x20/+0x28/+0x30` | bitstream | da `SetBitstreamFields` |
| `+0x70/+0x78/+0x80` | uscita | texture d'uscita, offset 0 |
| `+0x90/+0x98` | tabella di probabilità | anello `sessione + 0x1F0 + 8·[sessione+0x270]` |
| `+0xA0/+0xA8` | contesto | `sessione+0x1D0` |
| `+0xB0/+0xB8` | IT (solo con il flag DRM `0x80`) | `sessione+0x1E0` |

Per il VP9 Mesa usa gli stessi buffer: contesto con le probabilità predefinite, `rvcn_dec_vp9_probs_segment_t`
(2560 byte) nel `PROB_TBL_BUFFER`, che nel driver è l'anello AV1 `+0x1F0`. Va verificato che quell'anello sia
grande almeno 2560 byte (la struttura AV1 è più grande, quindi è atteso).

### DPB: Tier 1 come Mesa

Mesa su VCN ≤ 2.6 usa per il VP9 il **Tier 1** (`USE_DYNAMIC_DPB | USE_PAL`, messaggio `DYNAMIC_DPB 0x10`): 9 slot
in un solo buffer, ognuno luma `pitch·altezza` poi croma, con pitch, altezze e dimensioni scritti nel messaggio. Il
Tier 0 (DPB statico) chiede a Mesa 8192×4320×1,5×9 byte (~477 MB), perché il firmware usa allora un passo fisso:
troppo per un'APU e rischioso se sottodimensionato. Quindi:

- `CalculateDPBSize` = 9 × (luma + croma) con pitch e altezza allineati a 64 (×1,5 sui campioni a 10 bit);
- il messaggio VP9 ha le voci `DECODE`, `VP9 (0x0E, 120 B)`, `DYNAMIC_DPB (0x10, 116 B)`, più `DRM` e `0x0F`
  lasciati come fa il driver; `stream_type 0x11`, `decode_flags = 0x9`, `dpbArraySize = 9`, `dpbReserved0[0] = 64`.

### Patch previsti (tutti dietro `-NRedVP9`)

| # | Dove | Cosa |
| --- | --- | --- |
| 1 | `GetVAFunctionType` | `cmp rax, 0x1000000` → `0x1000`: il VP9 prende il tipo 6 (l'AV1 non esiste su VCN 2) |
| 2 | `ProcessVcnCreateDecodeSession` | codec di creazione `0x13` → `0x11` |
| 3 | `createDecodeDataProcessor` | `new(0x48)` → `new(0x48 + stato VP9)`: lo stato di `VP9Message::Session` vive nel processore |
| 4 | `AvdVAContext::DecodePicture` | tipo `0x1000` → ramo AV1 con bitstream `P+0/+8`, uscita `P+0x1250`, riferimenti `P+0x1260 + i·0x1258` |
| 5 | codice AV1 `0x427C0`–`0x45070` | i metodi della tabella sopra riscritti in C++ (`VP9Message.hpp` + collegamento), agli stessi indirizzi |

Il punto 5 è il più grande (~9 KB): serve un generatore nuovo che compili il backend come codice indipendente dalla
posizione, ne risolva le chiamate verso il driver per hash e lo spezzi nei blocchi del sistema di patch (oggi al
massimo 448 byte e 32 patch per gruppo, quindi un gruppo `UserVP9Decode` separato o blocchi più grandi).

### Ancora da verificare prima di scrivere il codice

- dimensione dell'anello di probabilità `sessione+0x1F0` e dei buffer del messaggio (`0x1008` basta: 0x18 + 5·16 +
  0xBC + 0x78 + 0x74 + 0x44 + 0xA8 = 0x2B4 byte);
- come il driver passa le superfici dei riferimenti: con il Tier 1 il VCN li legge dal DPB, quindi le superfici a
  `P+0x1260` servono solo a tenere allineati gli slot di `render_pic_list`;
- `swizzle` e `array_mode` del DPB e dell'uscita: copiare quelli che il driver usa per HEVC su VCN 2.

### V3 — Backend e cablaggio nel kext (14/11/2026, offline verde, non ancora provato sulla macchina)

**Pezzi.**

- `tools/vp9_backend/VP9Backend.cpp`: backend freestanding (Mesa porting, profilo 0 / 8 bit). Si compila con `-Oz`
  in 5819 B di codice, dentro i 6140 B liberi della regione AV1. Lo stato per sessione è di 224 B, in coda al
  `DecodeDataProcessor`.
- `tools/build_vp9_decode_patch.py`: collega il backend agli indirizzi della regione e verifica i byte originali
  degli agganci e i vtable slot. Scrive `NootedRed/UserVP9DecodeData.hpp`: 27 siti su 8 pagine, 5 funzioni
  verificate per intero, 186/44 riferimenti rel32 (originale/patchato), `SectionTargets` per il bersaglio dati di
  `libsystem_c`. `--check` confronta l'header con il generatore.
- `NootedRed/UserVP9DecodeResolver.hpp`: accetta solo la cache `31374756…16a1` e risolve con
  `sectionRelative=true` e i propri `SectionTargets`.
- `UserVideoDecode.cpp`, `VP9::install`:
  - Viene chiamato solo con `-NRedVP9`, su macOS 26, per processi non FairPlay, dopo che la patch video risulta
    `Applied` o `Already`.
  - La risoluzione completa avviene una volta per avvio (sotto `VP9::lock`), da pagine pulite.
  - Per ogni processo rilegge tutti i siti: se sono tutti originali li scrive con una seconda
    `SharedRegionTransaction`; se sono tutti patchati verifica che le pagine siano RX; se lo stato è misto non
    tocca nulla.
  - Un errore di scrittura disattiva VP9 fino al riavvio.

**Proprietà in ioreg.**

- `NRedVP9State`: 1 attivo, 2 cache verificata, 3 cache rifiutata, 4 commit fallito, 5 sistema non supportato,
  6 memoria insufficiente.
- `NRedVP9Applied` / `NRedVP9Already`: contatori, fino a 16.
- `NRedVP9Mixed`, `NRedVP9Region`, `NRedVP9CommitStatus`, `NRedVP9Rollback`: anomalie.

**Prove offline.**

- `test_vp9_backend.py`: il backend linkato gira su un driver finto. 198 messaggi uguali a quelli nativi; le
  guardie si comportano come previsto; due mutazioni vengono rilevate.
- `test_vp9_resolver_live.py`, sulla cache reale di questa macchina (sola lettura):
  - i 27 siti si risolvono agli offset generati;
  - a byte VP9 applicati il resolver video trova ancora tutto;
  - ogni funzione VP9 corrisponde alla variante patchata;
  - un UUID diverso viene rifiutato.
- **Limite noto.** Da pagine già patchate il resolver VP9 non ricostruisce i campi GOT originali coperti dalla
  regione (6 indiretti originali, 2 patchati), quindi rifiuta la risoluzione. È voluto: il kext risolve una sola
  volta da pagine pulite e in seguito confronta solo i byte dei siti.
- Build del kext Debug e Release senza nuovi warning.

**Restano aperti** (da verificare sulla macchina, solo con `tools/probe_vp9_decode.m`):

- se il firmware accetta il DPB Tier 1;
- se `VAVcnDecoder::Init` ha un controllo AV1 sulle capacità;
- la correttezza dei fotogrammi decodificati.

### V3 — Prima prova sulla macchina e gate delle capability (14/11/2026)

**Risultato della prima prova.** Avvio regolare, `NRedVP9State=2`, `NRedVP9Applied=16`. `probe_vp9_decode`:
`VTIsHardwareDecodeSupported=1`, ma `VTDecompressionSessionCreate=-12907` con
`GVA error: ailed to create context`. Con `-NRedVP9Trace` il backend non lascia nessuna traccia, mentre il
canale di traccia funziona (provato anche sotto il profilo sandbox di `VTDecoderXPC`): il driver non arriva mai
al backend.

**Causa.** La conclusione di V1 ("rifiuta `GetVAFunctionType`") era incompleta: prima ancora c'è un controllo di
AppleGVA.

- La funzione VP9 che crea il contesto (AppleGVA `0x…5dad80`) cerca nella lista capability del driver una voce
  `kind 1, type 0x1000` (ricerca di classe `0x80`, `0x…5aef4c`). Se non la trova restituisce `0xb` senza
  chiamare il driver.
- Poi controlla il formato del bitstream `'bwar'` (altrimenti `0xc`) e il formato di uscita, NV12 per 8 bit
  4:2:0 (altrimenti `0xd`).
- La lista viene da `AvdCapability::setupStaticCoreFunctions`: 41 funzioni, voci da `0x438` byte (`+0` kind,
  `+8` type, `+0x28` formati d'ingresso, `+0x22c` formati d'uscita, `+0x430` buddy). Nelle tabelle id/subId non
  c'è nessuna voce `1/0x1000`.
- La funzione 6 è AV1 (`1/0x1000000`): ingresso `'bwar'`, uscite NV12, `x420` e due varianti 10 bit, buddy 20 come
  HEVC. Sono proprio i formati che AppleGVA VP9 chiede.

**Correzione (ancora dietro `-NRedVP9`).** La funzione 6 diventa VP9. Tre agganci in più, quindi 30 siti su 9
pagine e codice backend 6081/6140 B:

5. In `setupStaticCoreFunctions` la `call getAVDFuncSubId` va a uno stub nella regione, `_vp9GetAVDFuncSubId`.
   Lo stub restituisce `0x1000` per l'indice 6 e salta all'originale per gli altri. La tabella sta in
   `__TEXT,__const`, dove il resolver non cerca, quindi non si patcha direttamente.
6. `AvdVAContext::GetBufferRequirement`: `cmp edx, 0x1000000` → `0x1000`. Senza questo cambio, il layout NV12
   del buffer d'uscita verrebbe rifiutato per il tipo `0x1000`.
7. `AvdVAInterface::GetContextParameter` (parametro `0xb`): `cmp r9, 0x1000000` → `0x1000`. VP9 legge i limiti
   che il kernel riporta per AV1.

`checkCapabilityList` resta com'è: per un tipo `0x1000` non applica controlli sulle dimensioni. Il dispositivo
smette di dichiarare AV1, che su VCN 2 non esiste comunque.

**Prove offline.** `test_vp9_backend`, `test_vp9_message`, `test_vp9_probe`, `test_vp9_resolver_live` e
`test_vpf_video_equivalence` sono verdi. L'hook 5 cambia solo il rel32 di una `call`: il confronto byte lo salta,
perché è verificato come riferimento, quindi il test live non chiede che la funzione smetta di combaciare con
l'originale. `test_video_cache_dynamic.py` era già rotto su HEAD per un harness non aggiornato
(`UserRejectedCaches`, VP9), indipendentemente da questo cambio.

### V3 — Decodifica in processo e primi fotogrammi corretti (14/11/2026)

**Debug senza riavvii.**
- `VTApplyRestrictions(1)` (esportata da VideoToolbox) fa girare il decoder dentro il processo chiamante.
  `probe_vp9_decode` la usa con `VP9_INPROCESS=1`, e con `VP9_DUMP=file` salva la luma di ogni fotogramma.
- Firmato con `get-task-allow`, il probe si può seguire con lldb. Le pagine del backend scritte dal kext sono
  visibili in ogni processo. Per provare un backend nuovo si scrivono i byte generati (`vp9-patch.json`) nella
  memoria del processo, senza reinstallare né riavviare.

**Altri due ostacoli, trovati così.**
- `AvdCapability::initDriverCapability` tiene una funzione solo se il bit del suo codec è acceso nella maschera
  hardware `[cap+0xb370]`. La funzione 6 controlla il bit 7 (AV1), assente su VCN 2, quindi non arrivava mai
  nella lista che vede AppleGVA, che conteneva 22 voci. Aggancio 8: la funzione 6 controlla il bit 6 (HEVC).
- Il driver usa il bitstream diretto (`cfg+0x94` bit `0x80`): il VCN legge in place il buffer di AppleGVA. Il
  backend lo rifiutava (traccia `Bitstream:0x100`). Ora fa come `AvdHevcDecDataProcessor::SetBitstreamFields`:
  passa al comando la texture sorgente e l'offset del frame al suo interno (qui 0).

**Risultato (lldb, backend scritto in memoria).** Su tutte le 8 clip a 8 bit (`probe-352x288`, `basic`,
`altref-cyclic-aq`, `complexity-aq-kf`, `error-resilient-odd` 350x198, `lossless`, `tiles-variance-aq` 1280x720,
`two-pass-altref`) ogni fotogramma viene decodificato senza errori. La luma è identica bit per bit a ffmpeg (piano
Y di `yuv420p`; `-pix_fmt gray` espande il range e falsa il confronto). La crominanza non è ancora confrontata.

**Generatore.**
- 31 siti su 9 pagine. Codice backend 6139/6140 B: per far posto a questi cambi ho tolto due tracce. Non resta
  margine.
- Corretto un bug latente: un sito della regione poteva finire a metà di un campo rel32 *originale*, e in quel
  caso il resolver del kext rifiutava la cache. Ora il taglio cade prima del campo, e il controllo copre anche i
  riferimenti originali.

### V3 — Prova col kext installato (14/11/2026)

- Avvio regolare, `NRedVP9State=2`. `probe_vp9_decode` nel modo normale (decoder in `VTDecoderXPCService`):
  `VTDecompressionSessionCreate = 0` su tutte le 8 clip a 8 bit. Tutti i fotogrammi sono decodificati e la luma è
  identica bit per bit a ffmpeg. Le tracce `NRedVP9T*` confermano che il decoder fuori processo passa dal backend.
- **Profilo 2 (10 bit).** `profile2-p010` creava la sessione e poi ogni fotogramma falliva con `-12909`.
  Correzione e prove nella sezione successiva.
- Ancora da fare: confronto della crominanza, clip più lunghe e risoluzioni maggiori (1080p/4K), cambio di
  risoluzione a metà stream.

### V3 — Profilo 2 rifiutato alla creazione (14/11/2026)

- Il `-12909` del profilo 2 veniva in parte dal probe, che dichiarava sempre `vpcC` profilo 0 a 8 bit. Ora il
  probe legge profilo e profondità dall'header del primo fotogramma, come fa un browser col suo `vpcC`.
- Con il `vpcC` corretto, il kext senza correzione accettava il profilo 2: sessione creata e 12 fotogrammi "ok",
  ma di correttezza non verificata. Il motivo: la funzione 6 (ex AV1) dichiarava anche uscite a 10 bit (`x420` e
  due varianti), oltre a NV12, e AppleGVA sceglie l'uscita a 10 bit per uno stream a 10 bit.
- **Aggancio 9.** In `setupStaticCoreFunctions` la `call getAVDFuncOutputFormatInfo` va allo stub
  `_vp9GetAVDFuncOutputFormatInfo`: per l'indice 6 usa il caso NV12 del driver (un solo formato), per gli altri
  indici chiama l'originale. Così AppleGVA rifiuta lo stream a 10 bit quando crea il contesto (`0xd`) e
  VideoToolbox restituisce `-12907`: un client che non chiede l'hardware obbligatorio ripiega sul software.
- Il generatore ora unisce più agganci nella stessa funzione (5 e 9 stanno in `setupStaticCoreFunctions`) in una
  sola funzione verificata. Le tracce di DPB, contesto e `initHWContext` sono state tolte per fare spazio: 32 siti
  su 9 pagine, codice 6103/6140 B.
- Prova (lldb, byte scritti in memoria): `profile2-p010` e `profile2-dither` → `VTDecompressionSessionCreate =
  -12907`. Le 8 clip a 8 bit restano identiche bit per bit a ffmpeg.
- Prova col kext installato (decoder in `VTDecoderXPCService`, 14/11/2026): le due clip a 10 bit danno
  `VTDecompressionSessionCreate = -12907`. Le 8 clip a 8 bit creano la sessione, nessun fotogramma viene scartato
  e la luma è identica a ffmpeg. `NRedVP9State` resta 2.

### V3 — Crominanza e clip 1080p (14/11/2026)

- Il probe con `VP9_DUMP` ora scrive, dopo la luma, anche il piano CbCr interlacciato (parte visibile), nello stesso
  formato di `ffmpeg -pix_fmt nv12`. Così si confrontano Y e CbCr separatamente.
- Nuove clip 1920x1080 generate in locale con libvpx-vp9 (`build/vp9-1080p/`): `rt-tiles` (realtime, 2 colonne di
  tile, 90 fotogrammi), `good-altref` (altref, lag 16, 60), `crf-noise-aq` (rumore, crf 30, aq 3, row-mt, 60),
  `bars60-gop20` (60 fps, GOP 20, 120).
- Prova col kext installato (decoder in `VTDecoderXPCService`): su 12 clip a 8 bit (le 8 precedenti più le 4 a
  1080p), 585 fotogrammi, nessuno scartato. **Luma e crominanza sono identiche bit per bit a ffmpeg** su ogni
  fotogramma, compresa la clip a dimensioni dispari 350x198.

### V3 — 4K, clip lunghe, cambio di risoluzione ed errori (14/11/2026)

**Prova.** Clip generate in locale con libvpx-vp9 (`build/vp9-4k/`, `build/vp9-long/`, `build/vp9-resize/`). Il
confronto con `ffmpeg -pix_fmt nv12` avviene in streaming con `tools/compare_vp9_decode.py`: il probe scrive il dump in
una FIFO e il confronto procede fotogramma per fotogramma, separando luma e crominanza. Così le clip lunghe non
richiedono dump da diversi GB.

**4K, col kext installato.** `rt-4k` 3840x2160 (realtime, 2 colonne di tile, 60 fotogrammi), `good-altref-4k`
3840x2160 (altref, row-mt, 30) e `dci-4096` 4096x2160 (rumore, 30): luma e crominanza identiche a ffmpeg su ogni
fotogramma.

**Clip lunghe.**
- `1080p-2min`: 3600 fotogrammi, GOP 150, rumore. Tutti identici a ffmpeg, in 55 s (circa 65 fps, dump e confronto
  compresi).
- `720p-5min-onekf`: 9000 fotogrammi con un solo keyframe, rumore. Tutti identici in 66 s: nessuna deriva lungo
  cinque minuti di riferimenti a catena.

**Oltre il limite: corruzione silenziosa, corretta.**
- `over-4352` 4352x2448: col kext installato la sessione veniva creata e ogni fotogramma risultava "ok", ma il
  contenuto era sbagliato (PSNR 4,9 dB). La creazione non marcava lo stato come valido, quindi ogni
  `SetBitstreamFields` falliva (traccia 0x100) e `VADecoder::DecodePicture` restituiva 3.
- Il ramo AV1 originale di `AvdVAContext::DecodePicture` ignora quel risultato: chiama comunque `setRefCon` e
  restituisce 0. Il fotogramma veniva quindi consegnato con una superficie che la VCN non aveva mai scritto.
- **Correzione 1.** `vp9ProcessCreateDecodeSession` restituisce 1 sopra i 4096 pixel, prima del messaggio di
  creazione del driver. VideoToolbox restituisce `-12907` alla creazione e il client ripiega sul software.
- **Correzione 2.** Se `VADecoder::DecodePicture` fallisce, `vp9DecodePicture` restituisce 5 (il codice d'errore del
  ramo originale) senza `setRefCon`. È stato provato forzando con lldb un ritorno 3 sul decimo fotogramma:
  - Prima della correzione: 30 fotogrammi "ok", di cui 20 corrotti (PSNR fino a 24 dB) senza alcun errore.
  - Con ritorno 1: i 10 fotogrammi precedenti restano corretti, poi 19 invii falliscono in modo sincrono con
    `-17690`.
  - Con ritorno 5: gli errori arrivano nel callback come `-12911` (`kVTVideoDecoderMalfunctionErr`).
  - In entrambi i casi AppleGVA considera finita la sessione e non chiama più il driver: il client deve ricrearla o
    ripiegare sul software. Si è scelto 5 perché il codice è documentato.
- Il generatore resta a 32 siti su 9 pagine, codice 6121/6140 B. L'harness offline ora verifica il rifiuto di
  4352x2448, 1920x4100 e 8192x4320 senza chiamate al driver, l'accettazione di 4096x2160 e il fallimento del
  fotogramma senza `setRefCon`.
- Le due correzioni sono provate prima in processo (lldb, byte scritti in memoria), poi col kext installato (decoder
  in `VTDecoderXPCService`, `NRedVP9State=2`). `over-4352` dà `VTDecompressionSessionCreate = -12907`. Le 16 clip a
  8 bit (comprese 1080p, 4K e `1080p-2min`) e `720p-5min-onekf` restano identiche a ffmpeg in luma e crominanza.

**Cambio di risoluzione a metà stream** (keyframe con la nuova dimensione):
- 640x360 → 1280x720: AppleGVA rifiuta ogni fotogramma più grande della sessione con `-12909`, prima del backend.
  L'errore è esplicito e i fotogrammi precedenti sono corretti.
- 1280x720 → 640x360: i fotogrammi sono decodificati e la zona 640x360 in alto a sinistra è identica a ffmpeg, ma
  AppleGVA li consegna in buffer 1280x720 con clean aperture 1280x720. Un client che non ricrea la sessione al cambio
  di formato mostrerebbe l'immagine piccola in un angolo. La dimensione del buffer la decide AppleGVA, non il
  backend: è un limite noto.
- Un fotogramma con l'header compresso corrotto viene scartato da AppleGVA con `-12909`; gli altri restano decodificati.

Il probe ora stampa dimensione del buffer e clean aperture del primo fotogramma e a ogni loro cambio.

### V4 — Prestazioni e decodifiche in parallelo (14/11/2026)

**Prova.** Kext installato, decoder in `VTDecoderXPCService`, Ryzen 5 4600H. Il probe con `VP9_PERF=1` decodifica in
modo asincrono, senza hash né dump, e stampa i fotogrammi al secondo. Il tempo di CPU è quello del probe più quello
di `VTDecoderXPCService`, letto con `ps` durante la prova. Le clip sono generate con libvpx: `4k60-10s` (3840x2160,
60 fps, 600 fotogrammi, 25 Mbit/s) e `1080p-2min` (3600 fotogrammi).

| Decodifica | 4K60, 10 s di video | 1080p, 2 minuti |
| --- | --- | --- |
| VCN | 4,2 s, 150 fps, CPU 0,9 s | 8,1 s, 470 fps, CPU 3,2 s |
| libvpx (ffmpeg `-c:v libvpx-vp9`) | 4,9 s, CPU 14,2 s | 8,6 s, CPU 24,3 s |
| decoder VP9 di ffmpeg | 2,5 s, CPU 17,9 s | 4,8 s, CPU 21,3 s |

- In hardware il 4K60 va a 2,5 volte il tempo reale con circa il 9% di un core, cioè 16–20 volte meno CPU del
  software. Il contenuto sintetico è facile per il software: con video reali il divario dovrebbe crescere.
- **In parallelo.** 2 e 4 sessioni 4K60 vanno a 75 e 38 fps ciascuna, 8 sessioni 1080p a 58 fps ciascuna. Il totale
  resta circa 150 fps 4K o 464 fps 1080p: la VCN è il collo di bottiglia e la divide in parti uguali, senza errori.
- **Correttezza in parallelo.** Sei clip decodificate insieme con `tools/compare_vp9_decode.py` (350x198, 1080p,
  4K, 4096x2160, `1080p-2min`) sono identiche a ffmpeg in luma e crominanza.
- **Con altri codec.** Durante due decodifiche VP9, tre giri di HEVC e H.264 1080p con `-hwaccel videotoolbox`
  danno `framemd5` identici al software. Anche le clip VP9 restano identiche.
- Due sessioni 4K60 insieme stanno appena sopra il tempo reale (75 fps ciascuna).

### V5 — Prima prova nei browser (14/11/2026)

**Prova.** Kext installato con `-NRedVP9Probe -NRedVP9 -NRedVP9Trace`. YouTube in Chrome e poi in Safari, mentre
`tools/monitor_vp9_browser.py` registra le tracce `NRedVP9T*`, la CPU dei processi e i log di VideoToolbox e del
kernel. La sessione è durata 5,5 minuti, con circa 5 minuti di riproduzione VP9 nei due browser.

**Chrome.**
- Le 5 sessioni VP9 vengono tutte dal processo GPU di Chrome, attraverso `VTDecoderXPCService`: quattro in 3840x2160 e
  una in 2560x1440. 7565 fotogrammi entrano in `DecodePicture`, senza rifiuti né fallimenti di
  `VADecoder::DecodePicture`.
- Ogni sessione si chiude in modo pulito (AppleGVA: pool, pipeline e acceleratori rilasciati). `NRedVP9State` resta 2.
- Nessun messaggio del kernel su VCN, reset della GPU o NootedRed. Immagine corretta e fluida, anche nei salti e nei
  cambi di qualità (osservazione dell'utente).
- All'avvio del video Chrome apre e chiude in pochi millisecondi cinque sessioni che AppleGVA rifiuta con `-12900`
  (`GVAVideoDecoder`, riga 1128, dopo "resolution constrains: 64x64"). Nessuna arriva al backend (nessuna traccia di
  creazione in quel momento): sembrano sondaggi di capacità di Chrome e la riproduzione parte normalmente subito dopo.

**Safari.**
- YouTube riceve `vp09`. WebKit decodifica dentro il proprio processo `com.apple.WebKit.GPU`, non in
  `VTDecoderXPCService`: per questo le tracce `NRedVP9T*` (registrate solo per `VTDecoderXPC*`) non lo vedono.
- I log di WebKit mostrano 7 sessioni VP9 di AppleGVA con `vp09 (HW decoder)`, in 1920x1080, 854x446 e 3840x2160, e
  nessun errore. Il kernel registra le chiamate di `com.apple.WebKit.GPU` al contesto video AMD (`AMDNavi10VideoContext`).

**CPU durante la registrazione** (5,5 minuti): `VTDecoderXPCService` 6,8 s (2,1% di un core), `com.apple.WebKit.GPU`
14,6 s (4,5%). Il resto è il lavoro abituale dei browser: `com.apple.WebKit.WebContent` 17,1% e Chrome 11,2%.

**Ancora da fare per chiudere V5:** un'ora di riproduzione, schede multiple e sospensione e risveglio con un video
aperto. Per avere anche Safari nei contatori, la traccia dovrebbe registrare anche `com.apple.WebKit.GPU`.

