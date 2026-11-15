# Piano dei prossimi miglioramenti

Piano di lavoro per ridurre il costo dei patch userspace sull'avvio dei processi e, per ultimo, i consumi del VCN.
Riferimento hardware: Ryzen 5 4600H, Radeon Renoir `0x1636` rev `0xC7`, macOS 26.7.1 (Darwin 25).
Il dettaglio dei lavori già fatti è in [Filtro processi e riuso FVRC](process-filter-fvrc.md).

## Situazione di partenza

| | Prima | Dopo filtro + FVRC | Dopo fase 1 | Dopo #11 (steve12311) |
| --- | --- | --- | --- | --- |
| `/usr/bin/true`, `/bin/ls` (saltati dal filtro) | ~43 ms | ~5 ms | ~5 ms | ~5 ms |
| Processo GPU patchato (`sips`, p50) | ~42 ms | ~31 ms | **~15 ms** | ~15,7 ms |
| Gruppi FVRC in cache per avvio | ~2 / 5 | 5 / 5 | 5 / 5 | 5 / 5 |
| Scansioni a vuoto sulla cache DriverKit per avvio (display / video) | — | 9 / 4 | **2 / 2** (fase 2) | 2 / 2 |
| Costo del kext per processo patchato (fase 0) | — | — | ~0,93 ms | ~1,04 ms |

Già fatti:

- **Cache FVRC** (`84e820d`): mappature `SM_COW` con shadow di base accettate; un risultato `HintOnly`
  di un processo già patchato non disattiva più una voce `Reusable`.
- **Filtro processi** (`84e820d`): i comandi di sistema che non collegano framework GPU o media non vengono patchati.
- **Fase 1** (`a1b890f`): header della cache letto una volta; seconda ricerca saltata sui processi già patchati.
- **Fase 2** (`e7ecf90`): le cache rifiutate (DriverKit) vengono ricordate per tutto l'avvio.
- **Fase 3** (`3c27adc`): la cache FVRC usa un `IOSimpleLock` del kernel al posto dell'attesa attiva.
- **PR #11 unita da steve12311** (`dd41818`, il 14/11/2026): le fasi 1–3 e il filtro sono nel progetto originale,
  con un suo commit di rifinitura (`92af8d9`): filtro a 81 percorsi esatti, proof su **tutti** i siti con confronto
  dell'header completo, snapshot con conteggio dei riferimenti, tabella dei rifiuti solo per rifiuti certi.
  Verificato sul Renoir: avvio regolare, costi uguali alla versione precedente (vedi fase 0).
- **Fase 0** (`bddfaaf`): cronometro per fase dietro `-NRedPhaseTiming`.
- **Fase 5** (`a34332d`): il VCN viene **già spento** da PowerPlay di Apple a riposo; aggiunto solo un contatore
  dietro `-NRedVCNPowerTrace`. Nessuna modifica al comportamento.

La ripartizione qui sotto è quella **prima della fase 1**, lasciata come riferimento. La fase 0 ha poi mostrato che
dopo le fasi 1–3 il kext costa ~1 ms per processo patchato: la differenza residua tra `sips` e `true` è il lavoro di
`sips` stesso (CoreImage, Metal), non NootedRed.

Restano circa **26 ms** per ogni processo patchato rispetto a uno saltato. Di questi:

- circa **10 ms** sono una seconda ricerca completa su processi già patchati (misurato: 5232 + 3391 letture,
  identiche a quelle riprodotte in user space su un processo già modificato);
- circa **4–5 ms** sono la ricerca con la cache pronta (misurato in user space);
- il resto, circa **10–12 ms**, è stimato: scrittura delle patch (copia COW delle pagine e cambi di permesso).

## Ordine di lavoro

| Fase | Intervento | Guadagno atteso | Rischio | Stato |
| --- | --- | --- | --- | --- |
| 0 | Cronometro per fase | nessuno (misura) | minimo | **fatto** (`bddfaaf`): kext ~1 ms per processo patchato |
| 1 | Saltare la seconda ricerca + scansione unica | ~10–12 ms per avvio GPU | basso | **fatto** (`a1b890f`): ~31 → ~15 ms |
| 2 | Ricordare le cache rifiutate | da misurare | basso | **fatto** (`e7ecf90`): 9/4 → 2/2 scansioni a vuoto |
| 3 | Lock spin del kernel nella cache FVRC | stabilità sotto carico | basso | **fatto** (`3c27adc`): tempi invariati, p90 più stabile |
| 4 | Meno lavoro per avvio | < 0,1 ms (misurato in fase 0) | basso | ridimensionata |
| 5 | Spegnimento VCN inattivo | batteria, non quantificato | alto | **chiusa** (`a34332d`): già fatto da PowerPlay, solo contatore |

Ogni fase finisce con: test offline verdi, build Release, installazione nell'EFI (prima una copia dell'EFI
sul Desktop, tenendo solo le più recenti), riavvio, verifica dei contatori in `ioreg` e nuova misura con `tools/bench_exec_latency.py`.
Una fase alla volta, ognuna in un commit separato, così un problema si può isolare.

### Prossimi passi (dopo le fasi 0–3, 5 e la PR #11)

Il piano originale è esaurito. Con la cache FVRC pronta un processo GPU patchato costa al kext **~1 ms** e uno già
patchato **~7 µs**; il VCN si spegne già a riposo. Restano lavori piccoli o di sola misura, in ordine di utilità:

1. **Costo all'avvio del sistema** (sola misura, rischio nullo): nei primi minuti dopo il boot la ricerca display arriva
   in media a 10–16 ms con picchi di secondi (cache FVRC vuota, carico ~25–50). È l'unico punto dove il kext costa
   ancora in modo visibile. Leggere `NRedPhase*` subito dopo il login e dopo 5 minuti per separare la finestra di boot,
   e capire se il tempo va nell'attesa del lease, nella CPU contesa o nella ricerca a freddo. Solo dopo decidere
   se intervenire (es. riempire la FVRC prima, o non attendere il lease per i processi di sistema).
2. **steve12311**: commento sulla PR #11 con la verifica su Renoir (testo preparato); eventualmente una nuova PR con
   la fase 0 (`bddfaaf`) e il contatore VCN (`a34332d`), entrambi spenti di default.
3. **Pulizia (ex fase 4)**, solo se si tocca comunque quel codice: `publish()` al posto di `setProp32` ripetuti
   (`NRedDRMVideoPID`, `*DynamicRejected`). Guadagno trascurabile; pool di buffer e filtro allargato non valgono il rischio.
4. **Punto aperto senza beneficio pratico:** l'UUID DriverKit visto in memoria (`E389CE92-…`) diverso da quello
   del file su disco (fase 2).

I boot-arg di diagnosi `-NRedPhaseTiming` e `-NRedVCNPowerTrace` restano attivi sul PC di riferimento per ricontrollare
tempi e ciclo del VCN dopo gli aggiornamenti di macOS; costano qualche contatore per processo.

---

## Fase 0 — Cronometro per fase

**Obiettivo:** sapere con precisione dove vanno i ~26 ms prima di ottimizzare la scrittura.

- In `UserPatchCoordinator::coordinate` e nei due `patchSharedRegion` misurare con `mach_absolute_time()`:
  lettura header, ricerca (Submit, Metal, video), preparazione della transazione, scrittura, ripristino permessi.
- Accumulare in contatori atomici (somma e massimo per fase, numero di campioni) e pubblicare in `ioreg`
  solo ogni 256 campioni, come già fanno `NRedFVRCHotTotal` e il filtro, per non tenere il lock del registro.
- Proprietà proposte: `NRedPhaseResolveUs`, `NRedPhasePrepareUs`, `NRedPhaseWriteUs`, `NRedPhaseSamples`.
- Attivabile solo con un boot-arg di debug (es. `-NRedPhaseTiming`) per non pagare nulla in uso normale.

**Fatto quando:** i tempi medi per fase spiegano i ~15 ms misurati dopo la fase 1 su un processo GPU patchato
(~10 ms sopra un processo saltato dal filtro).

### Stato: fatto (`bddfaaf`)

- `UserPhaseTiming.hpp`: 12 fasi (chiamata completa e saltata, header, controllo del salto, e per display e video
  la chiamata intera più ricerca, preparazione e scrittura). Spento costa un controllo di flag; acceso accumula somma,
  massimo e campioni in contatori atomici e pubblica `NRedPhase<Nome>AvgUs/MaxUs/Samples` alle prime 4 chiamate
  del coordinatore e poi ogni 64 (la proposta iniziale di 256 era troppo rada per i test brevi).
- **Lezione:** nel kext (LTO, `-fapple-kext`) le variabili `inline` di un header sono una copia per file `.cpp`.
  Il primo tentativo accendeva il flag solo nel modulo display e il coordinatore non pubblicava nulla; lo stato
  ora è in funzioni con `static` locali in `UserPatchCoordinator.cpp`. Lo stesso vale per FVRC e tabella dei rifiuti
  (una copia per modulo, voluto).
- Test: `tools/test_phase_timing.py` (spento silenzioso, media/massimo, `stop()` anticipato, `retarget`, cadenza).
- **Misurato** (medie solo sulle chiamate del benchmark, carico < 10; le medie totali includono il boot):

  | Fase | Fasi 1–3 (`3c27adc`) | Dopo #11 (`dd41818`) |
  | --- | --- | --- |
  | Chiamata completa | 0,93 ms | 1,04 ms |
  | Chiamata saltata | 4 µs | 7 µs |
  | Ricerca display / video | 1,0 / 0,5 ms | 1,19 / 0,57 ms |
  | Preparazione display / video | 0,04 / 0,01 ms | 0,1 / ~0 ms |
  | Scrittura display / video | 0,05 / 0,1 ms | ~0 / 0,08 ms |
  | `sips` p50 / p90 | 15,9 / 16,9 ms | 15,7 / 17,0 ms |

  Le differenze sotto ~50 µs non sono affidabili (medie pubblicate in µs interi su migliaia di campioni).
  Le proof più severe di #11 costano ~3 µs per salto, invisibili sui tempi reali.
- `-NRedPhaseTiming` resta un boot-arg di diagnosi: toglierlo quando non serve.

## Fase 1 — Saltare la seconda ricerca e unificare la scansione

**Problema 1:** lo stesso processo passa più volte dalla hook. Alla seconda il codice è già patchato:
la ricerca completa costa ~10 ms e non scrive nulla.

- Nel coordinatore, prima di chiamare i callback, riconoscere un processo già patchato.
  Due strade, da valutare in quest'ordine:
  1. **Registro per mappa VM:** il coordinatore ha già `UserPatchMapGate::Registry` indicizzato per `vm_map`.
     Aggiungere uno stato "patch completate" alla voce della mappa, azzerato quando la mappa viene distrutta
     o riusata (verificare come il registro gestisce oggi il riuso degli indirizzi `vm_map`).
  2. **Controllo dei byte:** leggere il primo sito di patch noto (dalla voce FVRC `Reusable`) e confrontarlo con il payload.
     Costa una sola lettura, ma serve la posizione già risolta.
- In caso di dubbio si continua a fare la ricerca completa, come oggi.

**Problema 2:** display (`UserSurfaceSync.cpp`) e video (`UserVideoDecode.cpp`) leggono ognuno l'header della cache
e l'elenco delle immagini.

- Il coordinatore legge una volta `base` e i 104 byte di header e li passa a entrambi i callback.
- Valutare se anche `UserSharedCache::Input` (immagini selezionate) si può condividere: i due moduli selezionano
  insiemi di immagini diversi, quindi probabilmente si condivide solo la parte di metadati.

**Test:**

- estendere `tools/test_shared_region_hooks.py` con una seconda chiamata sullo stesso processo,
  che non deve rifare la ricerca;
- `tools/test_display_coordinator_kext.py` per l'ordine display → video.

**Verifica sul PC:** `NRedImmediateSyncDynamicReads` non deve più mostrare 5232 a regime;
programma vuoto patchato da ~31 a circa 20 ms.

### 当前修复状态

共享缓存头仍只读取一次，proof 快捷路径保留并覆盖所有站点的完整替换字节及缓存头身份。
快照通过引用计数保护锁外核验和淘汰，分配失败时回退完整事务。
下面 `a1b890f` 的单站点 proof 描述与测量为历史记录；新版行为和边界见 process-filter-fvrc.md。
拒绝表只接受完整镜像表中缺少目标镜像的结果，不缓存一般解析失败。

### Stato: fatto (`a1b890f`)

- Il coordinatore legge una volta `base` e header e li passa ai due callback (`Transaction`); se la lettura
  fallisce ciascun modulo legge da sé come prima. L'elenco delle immagini resta per modulo (insiemi diversi).
- Ogni callback riporta `Patched` con una *proof* (fino a 16 byte del payload in un sito installato), `Idle`
  (nulla da fare per configurazione) o `Pending`. Con entrambi completati la `vm_map` viene ricordata (64 voci);
  alla chiamata successiva si salta solo se base uguale **e** le proof sono ancora in memoria. Questo protegge
  anche dal riuso dell'indirizzo `vm_map`; la voce viene comunque tolta dalla hook di distruzione della mappa.
- Contatore nuovo: `NRedPatchCoordinatorSkippedRuns`.
- Test: `tools/test_display_coordinator_kext.py` (salto con 4 letture, proof sparita, modulo incompleto,
  `forgetMap`, base diversa); `tools/dvpf/coordinator_probe.cpp` aggiornato alla nuova firma.
- **Misurato** (3 × 40 avvii a sistema calmo): `sips` p50 15.1–15.6 ms, p90 16.4–17.7 ms (prima ~31 ms);
  `true`/`ls` invariati (~5 ms). `NRedImmediateSyncDynamicReads` 1687 e `NRedVCNUserDynamicReads` 704
  (prima 5232 / 3391); oltre 3000 salti nei primi minuti.

## Fase 2 — Ricordare le cache rifiutate

`NRedImmediateSyncDynamicRejected=1` e `NRedSubmitGuardDynamicRejected=1` indicano che alcuni processi
vengono scansionati e poi rifiutati.

1. **Prima capire chi è:** pubblicare (solo in debug) PID e UUID della cache dell'ultimo rifiuto
   e confrontarli con `ps`. L'ipotesi "cache di DriverKit" non è verificata.
2. Se il rifiuto dipende solo dall'UUID della cache (non dal processo), tenere una piccola tabella fissa
   di UUID rifiutati (es. 4 voci) e saltare subito la ricerca per quegli UUID.
3. Se invece dipende dal processo, valutare se aggiungerlo al filtro processi.

**Fatto quando:** i rifiuti successivi al primo non costano più una scansione completa.

### Stato: fatto (`e7ecf90`)

- **Chi è:** i rifiuti vengono dai processi DriverKit (dext: Bluetooth seriale, HID, Dock seriale e processi brevi
  all'avvio); `vmmap` su un dext mostra librerie da `/System/DriverKit/...`. La cache principale
  (`31374756-89B4-34E8-A34F-56C5D57216A1`) non è mai stata rifiutata.
- **Punto aperto:** l'UUID rifiutato in memoria è `E389CE92-3D79-0598-912E-AC9773D7C691`, diverso da quello del file
  DriverKit nel cryptex (`DF6211BC-69BC-36F0-B1D1-B9EC846AC4B1`); non trovato in nessun file di cache su disco.
  Non cambia il funzionamento (la tabella usa l'UUID visto in memoria).
- `UserRejectedCaches.hpp`: tabella per avvio di 4 UUID, per modulo. Un UUID si salta solo dopo 2 rifiuti, mai se è
  già stato accettato; ogni 64 salti si rifà la ricerca e un'accettazione lo toglie dalla tabella.
- Diagnostica sempre attiva: `NRed*RejectCount`, `NRed*RejectSkips`, ultimo `NRed*RejectPID` e `NRed*RejectUUID0-3`.
  `-NRedRejectTrace` (tolto dai boot-args dopo la diagnosi) registra pid, nome, base e UUID dei primi 32 rifiuti;
  le righe vanno lette con `dmesg` (non arrivano a `log show`) e il buffer si riempie in fretta.
- Test: `tools/test_rejected_caches.py` (conferma, accettazione, ricontrollo, evizione, concorrenza con TSan).
- **Misurato:** scansioni a vuoto per avvio da 9 a 2 (display/SubmitGuard) e da 4 a 2 (video); 7 + 2 salti nel primo
  minuto. Beneficio piccolo (~0,05–0,2 s di CPU all'avvio, stimato), nessun effetto sull'apertura delle app;
  utile soprattutto se un dext va in crash e viene riavviato in loop.

## Fase 3 — Lock della cache FVRC

`VerifiedResolutionCache::acquire()` ([VerifiedResolutionCache.hpp:134](../NootedRed/VPF/VerifiedResolutionCache.hpp))
è un'attesa attiva fatta a mano che non disattiva la prelazione. Ora che la cache viene usata a ogni avvio
è sul percorso caldo: se il thread che tiene il lock viene sospeso, gli altri girano a vuoto
(al boot il carico medio arriva a ~50).

- Sostituire con `IOSimpleLock` / `lck_spin` (disattivano la prelazione), mantenendo sezioni critiche brevi:
  la copia di una voce (`CachedResolution`) è l'operazione più lunga.
- Per i test host (`tools/test_fvrc_registry.py`, concorrenza) lasciare un'implementazione alternativa
  dietro una macro o un parametro di template.

**Fatto quando:** il test di concorrenza passa con entrambe le implementazioni.

### Stato: fatto (`3c27adc`)

- Con `KERNEL` definito (build del kext) `acquire`/`release` usano `IOSimpleLockLock`/`IOSimpleLockUnlock`;
  il lock viene allocato una volta in `init()`. Se l'allocazione fallisce `ready` resta 0 e la cache è spenta
  (ricerca completa per ogni processo, come senza FVRC). Fuori dal kernel resta lo spin atomico.
- Verifica della build: il binario importa `_IOSimpleLockAlloc`, `_IOSimpleLockLock`, `_IOSimpleLockUnlock`
  (assenti senza la modifica), quindi nel kext è attiva la variante kernel.
- Test: `tools/test_fvrc_registry.py` compila entrambe le varianti (ASan/UBSan) ed esegue base, capienza e
  concorrenza; la variante kernel, con un `IOSimpleLock` simulato, verifica anche allocazione unica,
  lock/unlock per operazione e cache spenta se l'allocazione fallisce.
- **Misurato** su due avvii: `NRedFVRCEnabled`/`NRedFVRCCacheReady` = 1, tutti i gruppi "Hot";
  `sips` p50 14.4–15.3 ms (invariato rispetto alla fase 1), p90 16–17 ms nei giri a sistema calmo.
  Il beneficio atteso è la stabilità sotto carico, che il benchmark non misura direttamente.

## Fase 4 — Meno lavoro per avvio

**Ridimensionata dalla fase 0:** preparazione e scrittura insieme costano < 0,1 ms per processo con la cache pronta,
quindi pool di buffer e allocazioni valgono al massimo qualche decina di µs. Resta utile solo come pulizia.

- **Allocazioni:** `PatchWorkspace` (`UserSurfaceSync.cpp`) e i buffer video `TransactionBuffer`, `SavedBuffer`,
  `DynamicBuffer` (`UserVideoDecode.cpp`)
  vengono allocati con `IONew` a ogni avvio. Riusarli da un piccolo pool con lock, con fallback a `IONew`
  quando il pool è vuoto.
- **Proprietà invariate:** `NRedDRMVideoPID` (`UserVideoDecode.cpp`) e i `*DynamicRejected`
  chiamano `setProp32` senza il controllo "valore cambiato" usato da `publish()`. Passarli tutti da `publish()`.
- **Entitlement FairPlay:** `copyClientEntitlement` gira su ogni processo patchato; valutare se si può
  anticipare un controllo più economico o farlo solo quando i patch video sono effettivamente da applicare.

## Fase 5 — Spegnimento del VCN inattivo

Ipotesi iniziale: per Renoir (SMU12) il kext invia solo i messaggi di accensione e `smuInternalHwExit` è vuota,
quindi il VCN non verrebbe mai spento. **L'ipotesi era sbagliata**: `smuInternalHwExit` riguarda l'uscita del driver,
non lo spegnimento a riposo, che passa da PowerPlay.

### Stato: chiusa (`a34332d`, passo A, nessuna modifica di comportamento)

- **Cosa c'è nei driver Apple** (`AMDRadeonX5000HWLibs`, macOS 26, letto dal kernel collection):
  `PSM_ReArbit` chiama `PHM_PowerGateVCN(hwmgr, gate, instance)` con gate=0 quando c'è attività video e gate=1
  quando non c'è. `PHM_PowerGateVCN` salta al backend in `hwmgr+0x1328`; su Renoir è `PhwRenoir_PowerGateVCN`, che
  chiama `PhwRenoir_PowerDownVCN` (messaggio SMU `0xB`, come `PowerDownVcn` in Linux) o `PhwRenoir_PowerUpVCN`
  (`0xC`) e tiene lo stato in `*(hwmgr+0xA0)+0x3D0`. In `ioreg`: `EnableVCNDPG=1`, `CAIL_DisablePowerGating=0`.
- **Passo A** (`VCNPowerTrace.hpp`, `HWLibs.cpp`): con `-NRedVCNPowerTrace` un salto corto avvolge `PHM_PowerGateVCN`
  (il salto lungo copierebbe un `je` relativo), chiama sempre l'originale e conta. Dentro PowerPlay solo atomici;
  la pubblicazione avviene dal coordinatore userspace in contesto thread. Lo stato si legge solo se il backend è
  esattamente `PhwRenoir_PowerGateVCN`. Mai fatale: se la risoluzione fallisce `NRedVCNPowerTrace=0`.
- Test: `tools/test_vcn_power_trace.py` (passaggio invariato di argomenti e risultato, contatori, controllo del
  backend, pubblicazione solo sui cambi); stub di `publishPending` nei test del coordinatore.
- **Misurato** (riavvio, 1 min a riposo, video 20–30 s, 1 min a riposo): `NRedVCNPGBackend=2` (Renoir),
  4 accensioni, 3 spegnimenti, 0 errori, `NRedVCNPGGated=1` a riposo. Il ciclo funziona già.
- Non serve il passo B (invio manuale di `0xB`): avrebbe solo aggiunto il rischio di spegnere il VCN mentre il
  driver lo usa. Il consumo in batteria non è stato misurato: non c'è nulla da confrontare.
- Il contatore resta nel codice, spento di default, per controllare il ciclo dopo futuri aggiornamenti di macOS.

## Indagine video (14/11/2026)

Verifica di decodifica e codifica su Renoir con `ffmpeg` (clip di 10 s, 300 fotogrammi) e VideoToolbox. Nessun errore
nei log, nessuna modifica necessaria.

| Prova | Hardware (VCN) | Software (CPU) |
| --- | --- | --- |
| Decodifica H.264 4K | ~100 fps, 0,5 s di CPU | 8,7 s di CPU |
| Decodifica HEVC 8 bit 4K | ~80 fps, 0,5 s di CPU | 11,5 s di CPU |
| Decodifica HEVC 10 bit 4K | ~100 fps, 0,5 s di CPU | 21,4 s di CPU |
| Codifica H.264 / HEVC 1080p | ~150 / ~140 fps | x264 veryfast ~200 fps, 11,6 s di CPU |
| Codifica H.264 / HEVC 4K | ~52 / ~48 fps | — |
| Codifica HEVC Main10 4K | ~32 fps, uscita `yuv420p10le` | — |
| 3 decodifiche HEVC 4K + 1 codifica in parallelo | 9 s in tutto, nessun errore | — |

- **Avvio del decoder:** la prima sessione hardware di un processo costa ~30 ms; i ~100 ms in più visti con `ffmpeg`
  sono l'inizializzazione di VideoToolbox, uguale anche per una sessione software. Non dipende da NootedRed.
- **VP9:** non disponibile in hardware. `AMDRadeonVADriver2` ha decoder per AVC, HEVC, MPEG-2, VC-1, JPEG e AV1
  (solo VCN3), ma nessuno per VP9, anche se il VCN 2.2 di Renoir lo supporterebbe. Servirebbe scrivere un decoder:
  fuori portata.
- **AV1:** il VCN 2.2 di Renoir non lo supporta in hardware.
- **MPEG-2 / JPEG:** il driver ha il codice ma VideoToolbox non li espone come hardware; formati di nicchia, non vale
  il rischio.
- **Consiglio d'uso:** i video web in VP9 (es. YouTube nei browser che lo scelgono) vengono decodificati dalla CPU.
  Un'estensione che forza H.264 li sposta sul VCN, con meno consumi (YouTube offre H.264 fino a 1080p).
- **VP9 in hardware:** piano separato in [Decodifica VP9 in hardware](vp9-hw-decode-plan.md): AppleGVA ha già il
  decoder VP9, manca il backend `kAVDPictureDecodeVP9` nel driver AMD.

## Fuori piano

- **Alleggerire la ricerca con la cache pronta** (misurata ~0,6–1,2 ms in fase 0): toglierebbe parte della verifica completa dei byte
  che impedisce di patchare nel posto sbagliato. Guadagno piccolo, protezione persa.
- **Allargare il filtro processi** ai servizi in `/System/Library/...`: guadagno potenzialmente grande,
  ma richiede di valutare dipendenze da framework privati. Dopo la fase 0 il guadagno è ~1 ms per processo saltato,
  non ~10: poco interesse. Nella PR #11 steve12311 ha anzi ristretto il filtro a 81 percorsi esatti.
- **OpenGL:** controllare `NRedOpenGLDualQuadApplied` dopo aver aperto un'app OpenGL; è una verifica,
  non un miglioramento.
