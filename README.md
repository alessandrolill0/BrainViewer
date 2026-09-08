# BrainViewer

An interactive installation: a 3D brain rendered in TouchDesigner as a point cloud, which
follows the movement of a person captured by the webcam and activates lobe by lobe
according to the musical content of the tracks that are loaded. The visitor controls
everything from a single web screen: pick a track, watch the brain, read the report on the
dominant area.

## Architecture

```
   ┌─────────────┐   WebSocket + HTTP   ┌──────────────┐   OSC/UDP   ┌────────────────┐
   │    Panel    │◄────────────────────►│   Backend    │────────────►│ TouchDesigner  │
   │ React/Vite  │                      │   FastAPI    │             │ audio + render │
   │   :5173     │                      │    :8000     │             │  :9000  :9001  │
   └──────▲──────┘                      └──────────────┘             └───────┬────────┘
          │                                                                   │
          └───────────────── WebRTC (video, direct) ──────────────────────────┘
```

Every piece of data goes through the backend; only the render video travels from
TouchDesigner to the browser directly, over WebRTC, with the backend relaying the
negotiation.

| Port | Operator in TD | Content |
|---|---|---|
| **9000** | OSC In **CHOP** | number streams: `/lobes/activation`, `/lobes/sync`, `/pose/*` |
| **9001** | OSC In **DAT** | text commands: `/song/*` |

## Layout

```
backend/app/           FastAPI: library, audio analysis, pose tracking, mapping, OSC
frontend/src/          React + Vite: interface only
touchdesigner/         the .toe, the Python callbacks and the geometry of the 7 regions
tools/                 setup scripts: models, camera permission
```

Backend modules, in dependency order: `osc_client` → `playback` → `library` → `analysis` →
`pose` / `expression` → `mapping` → `lobes` / `signaling` → `main`.

## Setup

### Environment

The Python environment is **conda**.

```bash
conda env create -f environment.yml
```

### Models

Valence and arousal, and the facial expression reader, need models that are not bundled
with the packages:

```bash
conda activate brain_viewer && python tools/download_models.py
```

Without them the analysis still runs: mood falls back to a heuristic, and the `mood.source`
field says which was used.

### Camera permission (macOS, once)

```bash
conda activate brain_viewer && python tools/check_camera.py
```

## Running

### Backend

```bash
conda activate brain_viewer && cd backend && uvicorn app.main:app --reload --port 8000
```

Optional variables: `TD_OSC_HOST` (default `127.0.0.1`), `TD_OSC_PORT` (`9000`),
`TD_OSC_EVENT_PORT` (`9001`).

### Frontend

```bash
cd frontend && npm install && npm run dev
```

Opens at http://localhost:5173. Copy `frontend/.env.example` to `frontend/.env` to
configure the render feed and the backend address.

### TouchDesigner

Open `touchdesigner/3D_model/brain.toe`. The geometry of the seven regions lives in
`touchdesigner/exports/regions/` (one OBJ per region plus `regions.json`), and the
instanced neuron is `touchdesigner/3D_model/single_neuron.obj`.

The Python callbacks go into their respective Callbacks DATs: `song_osc_callbacks.py` on the
OSC In DAT, `webrtc_signaling.py` on the WebSocket DAT, `webrtc_callbacks.py` on the
WebRTC DAT.

## Using the panel

Drop an audio file on the upload area, or click it to browse. The analysis starts on its
own and takes a few seconds; the track is playable as soon as it is marked `ANALYZED`.
Click a track to play it, hover a row and click `×` twice to delete it.

Three cells of the status bar are clickable:

| Cell | Action |
|---|---|
| **WEBCAM** | turns face tracking on and off |
| **TRACKING** | sets the current pose as neutral (sit upright and click) |
| **SCREEN** | fullscreen |

With the webcam off, the model is driven by **dragging on the render box**: horizontal
rotates, vertical tilts, the wheel moves closer. Webcam and dragging cannot be used at the
same time.

Clicking a region on the sector map or on the wireframe locks the report on it; clicking
again unlocks it.

## Analysis

The analysis runs once per track, in a thread, and is stored in the library index. It
extracts bpm, beats and confidence, key, descriptors, mood, and a **timeline**: five 0-1
curves sampled at 2 Hz (`energy`, `dynamics`, `percussive`, `brightness`, `novelty`) that
say how the track is going at any given moment, and that drive the lobes.

**Essentia** does the signal work, **librosa** the local curves, **TensorFlow** the mood.

Uploaded files end up in `backend/uploads/` with a JSON index, stored as
`<track_id>.<ext>`. Uploading the same content twice does not create a duplicate.

## Tools

| Script | Purpose |
|---|---|
| `tools/download_models.py` | downloads the TensorFlow and Face Landmarker models |
| `tools/check_camera.py` | unlocks camera permission on macOS |
