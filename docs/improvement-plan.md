# Piano dei prossimi miglioramenti

Piano di lavoro per ridurre il costo dei patch userspace sull'avvio dei processi e, per ultimo, i consumi del VCN.
Riferimento hardware: Ryzen 5 4600H, Radeon Renoir `0x1636` rev `0xC7`, macOS 26.7.1 (Darwin 25).
Il dettaglio dei lavori già fatti è in [Filtro processi e riuso FVRC](process-filter-fvrc.md).

## Situazione di partenza

| | Prima | Dopo filtro + FVRC | Dopo fase 1 |
| --- | --- | --- | --- |
| `/usr/bin/true`, `/bin/ls` (saltati dal filtro) | ~43 ms | ~5 ms | ~5 ms |
| Processo GPU patchato (`sips`, p50) | ~42 ms | ~31 ms | **~15 ms** |
| Gruppi FVRC in cache per avvio | ~2 / 5 | 5 / 5 | 5 / 5 |
| Scansioni a vuoto sulla cache DriverKit per avvio (display / video) | — | 9 / 4 | **2 / 2** (fase 2) |

Già fatti:

- **Cache FVRC** (`84e820d`): mappature `SM_COW` con shadow di base accettate; un risultato `HintOnly`
  di un processo già patchato non disattiva più una voce `Reusable`.
- **Filtro processi** (`84e820d`): i comandi di sistema che non collegano framework GPU o media non vengono patchati.
- **Fase 1** (`a1b890f`): header della cache letto una volta; seconda ricerca saltata sui processi già patchati.
- **Fase 2** (`e7ecf90`): le cache rifiutate (DriverKit) vengono ricordate per tutto l'avvio.

La ripartizione qui sotto è quella **prima della fase 1**, lasciata come riferimento.

Restano circa **26 ms** per ogni processo patchato rispetto a uno saltato. Di questi:

- circa **10 ms** sono una seconda ricerca completa su processi già patchati (misurato: 5232 + 3391 letture,
  identiche a quelle riprodotte in user space su un processo già modificato);
- circa **4–5 ms** sono la ricerca con la cache pronta (misurato in user space);
- il resto, circa **10–12 ms**, è stimato: scrittura delle patch (copia COW delle pagine e cambi di permesso).

## Ordine di lavoro

| Fase | Intervento | Guadagno atteso | Rischio | Stato |
| --- | --- | --- | --- | --- |
| 0 | Cronometro per fase | nessuno (misura) | minimo | da fare |
| 1 | Saltare la seconda ricerca + scansione unica | ~10–12 ms per avvio GPU | basso | **fatto** (`a1b890f`): ~31 → ~15 ms |
| 2 | Ricordare le cache rifiutate | da misurare | basso | **fatto** (`e7ecf90`): 9/4 → 2/2 scansioni a vuoto |
| 3 | Lock spin del kernel nella cache FVRC | stabilità sotto carico | basso | da fare |
| 4 | Meno lavoro per avvio | pochi ms | basso | da fare |
| 5 | Spegnimento VCN inattivo | batteria, non quantificato | alto | da fare |

Ogni fase finisce con: test offline verdi, build Release, installazione nell'EFI (backup già sulla chiavetta
TAHOE-USB), riavvio, verifica dei contatori in `ioreg` e nuova misura con `tools/bench_exec_latency.py`.
Una fase alla volta, ognuna in un commit separato, così un problema si può isolare.

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

## Fase 4 — Meno lavoro per avvio

Da decidere con i numeri della fase 0.

- **Allocazioni:** `PatchWorkspace` (`UserSurfaceSync.cpp`) e i buffer video `TransactionBuffer`, `SavedBuffer`,
  `DynamicBuffer` (`UserVideoDecode.cpp`)
  vengono allocati con `IONew` a ogni avvio. Riusarli da un piccolo pool con lock, con fallback a `IONew`
  quando il pool è vuoto.
- **Proprietà invariate:** `NRedDRMVideoPID` (`UserVideoDecode.cpp`) e i `*DynamicRejected`
  chiamano `setProp32` senza il controllo "valore cambiato" usato da `publish()`. Passarli tutti da `publish()`.
- **Entitlement FairPlay:** `copyClientEntitlement` gira su ogni processo patchato; valutare se si può
  anticipare un controllo più economico o farlo solo quando i patch video sono effettivamente da applicare.

## Fase 5 — Spegnimento del VCN inattivo (esperimento separato)

Per Renoir (SMU12) il kext invia solo i messaggi di accensione e `smuInternalHwExit` è vuota:
il VCN non viene mai spento. Linux usa `PowerDownVcn` / `PowerUpVcn`.

1. Ricavare i numeri dei messaggi da `renoir_ppsmc.h` di Linux e verificarli contro quelli già usati dal kext.
2. Capire quando il driver Apple considera il VCN inattivo e dove agganciarsi senza interferire con
   decodifica e codifica in corso.
3. Prima prova dietro un boot-arg spento di default (es. `-NRedVCNPowerGate`), con backup dell'EFI pronto.
4. Misurare il consumo in batteria (es. `powermetrics` o stima della batteria a riposo) con e senza.

**Rischi:** blocco della decodifica, schermo nero al risveglio, panic. Non va unito ad altre modifiche.

## Fuori piano

- **Alleggerire la ricerca con la cache pronta** (~2–3 ms): toglierebbe parte della verifica completa dei byte
  che impedisce di patchare nel posto sbagliato. Guadagno piccolo, protezione persa.
- **Allargare il filtro processi** ai servizi in `/System/Library/...`: guadagno potenzialmente grande,
  ma richiede di valutare dipendenze da framework privati. La fase 1 è fatta: si può riconsiderare,
  partendo da `python3 tools/test_process_filter.py --live --verbose`.
- **OpenGL:** controllare `NRedOpenGLDualQuadApplied` dopo aver aperto un'app OpenGL; è una verifica,
  non un miglioramento.
