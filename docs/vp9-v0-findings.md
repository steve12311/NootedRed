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
