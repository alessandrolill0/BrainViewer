# BrainViewer

Installazione interattiva: un cervello 3D a nuvola di punti renderizzato in TouchDesigner,
che segue il movimento di una persona ripresa dalla webcam e si attiva per lobi in base al
contenuto musicale dei brani caricati. Il visitatore controlla tutto da un'unica schermata
web: sceglie il brano, guarda il cervello, legge il referto sull'area dominante.

## Architettura

```
   ┌─────────────┐   WebSocket + HTTP   ┌──────────────┐   OSC/UDP   ┌────────────────┐
   │  Pannello   │◄────────────────────►│   Backend    │────────────►│ TouchDesigner  │
   │ React/Vite  │                      │   FastAPI    │             │ audio + render │
   │   :5173     │                      │    :8000     │             │  :9000  :9001  │
   └──────▲──────┘                      └──────────────┘             └───────┬────────┘
          │                                                                   │
          └───────────────── WebRTC (video, diretto) ─────────────────────────┘
```

**Regola di flusso: il frontend non parla mai direttamente con TouchDesigner.** Ogni dato
passa dal backend. L'unica deroga è il video del render, che va da TD al browser via WebRTC
senza attraversare Python — farlo transitare costerebbe più di quanto vale. Il backend
resta comunque il centralino della sola negoziazione.

Le porte OSC sono **due** perché l'OSC In CHOP di TouchDesigner scarta le stringhe, e due
operatori non possono condividere una porta UDP:

| Porta | Operatore in TD | Contenuto |
|---|---|---|
| **9000** | OSC In **CHOP** | flussi di numeri: `/lobes/activation`, `/lobes/sync`, `/pose/*` |
| **9001** | OSC In **DAT** | comandi testuali: `/song/*` |

⚠️ **L'ordine dei sette argomenti di `/lobes/activation` è un contratto**: è l'ordine dei
campioni nel Lookup CHOP di TouchDesigner, e cambiarlo sposta ogni neurone sul lobo
sbagliato senza produrre nessun errore. L'ordine viene da
`touchdesigner/exports/regions/regions.json`, generato da `tools/split_lobes.py`.

## Struttura

```
backend/app/           FastAPI: libreria, analisi audio, pose tracking, mapping, OSC
frontend/src/          React + Vite: solo interfaccia
touchdesigner/         il .toe, le callback Python e la geometria delle 7 regioni
tools/                 script una-tantum: segmentazione, modelli, calibrazione, collaudo
```

I moduli del backend, in ordine di dipendenza: `osc_client` → `playback` → `library` →
`analysis` → `pose` / `expression` → `mapping` → `lobes` / `signaling` → `main`.
Il cuore artistico è **`backend/app/mapping.py`**: ogni costante è commentata con la sua
motivazione.

## Avvio

### Backend

L'ambiente Python è **conda**, non venv, ed è definito in [environment.yml](environment.yml).

```bash
conda env create -f environment.yml
```

```bash
conda activate brain_viewer && cd backend && uvicorn app.main:app --reload --port 8000
```

⚠️ **Python 3.10 non è negoziabile.** Essentia pubblica wheel macOS solo per x86_64 e solo
fino a cp310: le cp311 e cp312 si installano ma vanno in `SIGABRT` all'import. Per lo stesso
motivo `librosa` e `ffmpeg` stanno fra le dipendenze conda e non pip — `llvmlite` non ha più
wheel per macOS x86_64 e pip proverebbe a compilarlo dai sorgenti.

Variabili opzionali: `TD_OSC_HOST` (default `127.0.0.1`), `TD_OSC_PORT` (`9000`),
`TD_OSC_EVENT_PORT` (`9001`).

### Frontend

```bash
cd frontend && npm install && npm run dev
```

Apre su http://localhost:5173. Copia `frontend/.env.example` in `frontend/.env` per
configurare il feed del render e l'indirizzo del backend.

Tutto ciò che la UI mostra passa da un solo punto, `src/hooks/useTelemetry.js`: WebSocket
per gli eventi spinti dal backend, polling di `/api/health` per i valori continui. Nessun
componente conosce la rete.

Tre celle della barra di stato sono cliccabili, invece di aggiungere pulsanti fuori dal
sistema del design:

| Cella | Azione |
|---|---|
| **WEBCAM** | accende e spegne il tracciamento del volto |
| **TRACKING** | fissa la posa corrente come neutra (stai dritto e clicca) |
| **SCHERMO** | schermo intero |

Con la webcam spenta si comanda il modello **trascinando sul riquadro del render**:
orizzontale ruota, verticale inclina, la rotella avvicina. I valori partono sugli stessi
indirizzi OSC del tracciamento, quindi TouchDesigner non distingue le due sorgenti — ed è
per questo che non possono convivere.

### TouchDesigner

Il progetto è `touchdesigner/3D_model/brain.toe`. La geometria delle sette regioni sta in
`touchdesigner/exports/regions/` (un OBJ per regione più `regions.json`), il neurone
istanziato è `touchdesigner/3D_model/single_neuron.obj`.

Due file di configurazione letti da TD come Table DAT:

- **`palette.tsv`** — colore RGB di ogni regione, nell'ordine dei `lobe_id`
- **`entrainment.tsv`** — quanto ogni lobo si aggancia al battito (cervelletto 1.00,
  occipitale 0.00)

⚠️ Dopo averli modificati va premuto il **Pulse** sul Table DAT: TouchDesigner tiene una
copia in memoria e non rilegge il file da solo.

Le callback Python vanno nei rispettivi Callbacks DAT:
`song_osc_callbacks.py` sull'OSC In DAT, `webrtc_signaling.py` sul WebSocket DAT,
`webrtc_callbacks.py` sul WebRTC DAT.

## Verifica rapida

```bash
curl http://localhost:8000/api/health
```

Restituisce stato OSC, riproduzione, attivazione dei sette lobi, sincronia, posa ed
espressione: è il primo posto da guardare quando qualcosa non torna.

### Collaudo dell'attivazione dei lobi

Manda `/lobes/activation` a 30 Hz senza bisogno di analisi audio, per verificare la catena
OSC → Lookup CHOP → instancing.

Un lobo alla volta, a rotazione:

```bash
curl -X POST http://localhost:8000/api/test/lobes/sweep
```

Una sola regione, accesa e lasciata accesa — serve a orientare la scena e a verificare che
le regioni cadano dove devono:

```bash
curl -X POST http://localhost:8000/api/test/lobes/only/frontal
```

Stop (manda un ultimo pacchetto di zeri, così i lobi non restano accesi):

```bash
curl -X POST http://localhost:8000/api/test/lobes/off
```

## Libreria e analisi

I file caricati finiscono in `backend/uploads/` con un indice JSON. Non è versionata: è
roba dell'utente. Il file viene salvato come `<track_id>.<estensione>` e non col nome
originale, perché quel percorso viaggia in OSC e TouchDesigner deve riaprirlo: spazi e
accenti sono solo un rischio. Caricare due volte lo stesso contenuto non crea un doppione,
viene riconosciuto dall'hash.

```bash
curl -X POST http://localhost:8000/api/upload -F 'file=@/percorso/al/brano.wav'
curl http://localhost:8000/api/library
curl -X POST http://localhost:8000/api/library/select/TRACK_ID
curl -X DELETE http://localhost:8000/api/tracks/TRACK_ID
```

L'analisi parte **da sola dopo l'upload**, gira in un thread e ci mette una decina di
secondi per un brano di due minuti. Per rilanciarla:

```bash
curl -X POST http://localhost:8000/api/tracks/TRACK_ID/analyze
```

Estrae bpm, beat e confidenza, tonalità, sezioni strutturali (intro/verse/chorus/bridge/
outro), descrittori e mood. **Essentia** fa il lavoro sul segnale, **librosa** solo la
segmentazione strutturale, **TensorFlow** il mood.

### Modelli per il mood

Valence e arousal usano due modelli TensorFlow di Essentia che non sono inclusi nel
pacchetto:

```bash
conda activate brain_viewer && python tools/download_models.py
```

Senza di essi l'analisi funziona lo stesso: il mood ricade su un'euristica pesata sui
descrittori, e il campo `mood.source` dice sempre quale delle due è stata usata.

## Pose tracking (webcam)

Una volta sola, per concedere il permesso della fotocamera — su macOS la richiesta non può
partire dal thread secondario in cui il backend apre la webcam:

```bash
conda activate brain_viewer && python tools/check_camera.py
```

Poi:

```bash
curl -X POST http://localhost:8000/api/pose/start
curl -X POST http://localhost:8000/api/pose/calibrate
curl -X POST http://localhost:8000/api/pose/stop
```

Lo stato (`present`, `x`, `y`, `scale`, `energy`, `lean`, `turn`, `pitch`, `fps`) compare in
`/api/health`.

⚠️ **L'interazione è uno specchio**: il fotogramma viene ribaltato nel backend, una volta
sola, prima di MediaPipe. Da lì in poi tutto è in coordinate schermo e **in TouchDesigner
le traslazioni non vanno invertite**. Fanno eccezione le rotazioni attorno alla verticale:
uno specchio inverte il verso delle rotazioni attorno agli assi che giacciono nel suo piano,
quindi *Rotate Y* del `geo1` usa un moltiplicatore negativo.

## Controllo della riproduzione

L'audio lo suona **TouchDesigner**, non il browser: l'analisi spettrale e il beat detection
devono essere sincroni col rendering. I comandi vanno sulla porta OSC **9001**.

```bash
curl -X POST http://localhost:8000/api/playback/play
curl -X POST http://localhost:8000/api/playback/pause
curl -X POST http://localhost:8000/api/playback/seek   -H 'Content-Type: application/json' -d '{"value": 42.5}'
curl -X POST http://localhost:8000/api/playback/volume -H 'Content-Type: application/json' -d '{"value": 0.8}'
```

La posizione di riproduzione è **stimata** dall'orologio del backend: TouchDesigner non la
comunica indietro. Basta, perché il mapping lavora su sezioni da decine di secondi. La fine
del brano si deduce da durata nota più posizione stimata; a fine brano si ferma e torna a
inizio, non passa al successivo.

L'OSC va su UDP, quindi il backend non fallisce se TouchDesigner non è aperto.

## Strumenti

| Script | A cosa serve |
|---|---|
| `tools/split_lobes.py` | dal modello FBX segmentato ai 7 OBJ per regione |
| `tools/make_wireframe.py` | dai 7 OBJ al disegno anatomico del pannello |
| `tools/download_models.py` | scarica i modelli TensorFlow e Face Landmarker |
| `tools/check_camera.py` | sblocca il permesso fotocamera su macOS |
| `tools/compare_tracks.py` | confronta il mapping fra i brani in libreria |
| `tools/preview_regions.py` | PNG di controllo della segmentazione |
| `tools/bench_face.py` | misura il costo dei due tracciatori MediaPipe |
