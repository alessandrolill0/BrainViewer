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

**Flow rule: the frontend never talks to TouchDesigner directly.** Every piece of data goes
through the backend. The one exception is the render video, which travels from TD to the
browser over WebRTC without passing through Python — routing it there would cost more than
it is worth. The backend still relays the negotiation itself.

There are **two** OSC ports because TouchDesigner's OSC In CHOP discards strings, and two
operators cannot share a UDP port:

| Port | Operator in TD | Content |
|---|---|---|
| **9000** | OSC In **CHOP** | number streams: `/lobes/activation`, `/lobes/sync`, `/pose/*` |
| **9001** | OSC In **DAT** | text commands: `/song/*` |


## Layout

```
backend/app/           FastAPI: library, audio analysis, pose tracking, mapping, OSC
frontend/src/          React + Vite: interface only
touchdesigner/         the .toe, the Python callbacks and the geometry of the 7 regions
tools/                 one-off scripts: segmentation, models, calibration, testing
```

Backend modules, in dependency order: `osc_client` → `playback` → `library` → `analysis` →
`pose` / `expression` → `mapping` → `lobes` / `signaling` → `main`.
The artistic core is **`backend/app/mapping.py`**: every constant is commented with its
rationale.

## Running

### Backend

The Python environment is **conda**.

```bash
conda env create -f environment.yml
```

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

Everything the UI shows goes through a single point, `src/hooks/useTelemetry.js`: a
WebSocket for events pushed by the backend, polling of `/api/health` for continuous values.
No component knows about the network.

Three cells of the status bar are clickable, instead of adding buttons outside the design
system:

| Cell | Action |
|---|---|
| **WEBCAM** | turns face tracking on and off |
| **TRACKING** | sets the current pose as neutral (sit upright and click) |
| **SCHERMO** | fullscreen |

With the webcam off, the model is driven by **dragging on the render box**: horizontal
rotates, vertical tilts, the wheel moves closer. The values leave on the same OSC addresses
as the tracking, so TouchDesigner cannot tell the two sources apart — which is also why
they cannot run at the same time.

### TouchDesigner

The project is `touchdesigner/3D_model/brain.toe`. The geometry of the seven regions lives
in `touchdesigner/exports/regions/` (one OBJ per region plus `regions.json`), and the
instanced neuron is `touchdesigner/3D_model/single_neuron.obj`.

Two configuration files read by TD as Table DATs:

- **`palette.tsv`** — RGB colour of each region, in `lobe_id` order
- **`entrainment.tsv`** — how much each lobe locks onto the beat (cerebellum 1.00,
  occipital 0.00)

After editing them you must hit **Pulse** on the Table DAT: TouchDesigner keeps a copy in
memory and does not re-read the file on its own.

The Python callbacks go into their respective Callbacks DATs: `song_osc_callbacks.py` on the
OSC In DAT, `webrtc_signaling.py` on the WebSocket DAT, `webrtc_callbacks.py` on the
WebRTC DAT.

## Quick check

```bash
curl http://localhost:8000/api/health
```

Returns OSC status, playback, activation of the seven lobes, synchrony, pose and
expression: the first place to look when something is off.

### Testing lobe activation

Sends `/lobes/activation` at 30 Hz with no audio analysis needed, to verify the
OSC → Lookup CHOP → instancing chain.

One lobe at a time, cycling:

```bash
curl -X POST http://localhost:8000/api/test/lobes/sweep
```

A single region, lit and left lit — useful to orient the scene and check that the regions
land where they should:

```bash
curl -X POST http://localhost:8000/api/test/lobes/only/frontal
```

Stop (sends one last packet of zeros, so the lobes do not stay lit):

```bash
curl -X POST http://localhost:8000/api/test/lobes/off
```

## Library and analysis

Uploaded files end up in `backend/uploads/` with a JSON index. It is not versioned: it is
the user's material. Files are stored as `<track_id>.<ext>` rather than under their original
name, because that path travels over OSC and TouchDesigner has to reopen it: spaces and
accents are only a risk. Uploading the same content twice does not create a duplicate, it is
recognised by hash.

```bash
curl -X POST http://localhost:8000/api/upload -F 'file=@/path/to/track.wav'
curl http://localhost:8000/api/library
curl -X POST http://localhost:8000/api/library/select/TRACK_ID
curl -X DELETE http://localhost:8000/api/tracks/TRACK_ID
```

The analysis **starts on its own after the upload**, runs in a thread and takes about ten
seconds for a two-minute track. To re-run it:

```bash
curl -X POST http://localhost:8000/api/tracks/TRACK_ID/analyze
```

It extracts bpm, beats and confidence, key, descriptors, mood, and a **timeline**: five
0-1 curves sampled at 2 Hz (`energy`, `dynamics`, `percussive`, `brightness`, `novelty`)
that say how the track is going at any given moment. That is what drives the lobes — the
mapping follows the music itself rather than a structural label. **Essentia** does the
signal work, **librosa** the local curves, **TensorFlow** the mood.

### Mood models

Valence and arousal use two Essentia TensorFlow models that are not bundled with the
package:

```bash
conda activate brain_viewer && python tools/download_models.py
```

Without them the analysis still works: mood falls back to a weighted heuristic over the
descriptors, and the `mood.source` field always says which of the two was used.

## Pose tracking (webcam)

Once only, to grant camera permission — on macOS the request cannot come from the worker
thread in which the backend opens the webcam:

```bash
conda activate brain_viewer && python tools/check_camera.py
```

Then:

```bash
curl -X POST http://localhost:8000/api/pose/start
curl -X POST http://localhost:8000/api/pose/calibrate
curl -X POST http://localhost:8000/api/pose/stop
```

The state (`present`, `x`, `y`, `scale`, `energy`, `lean`, `turn`, `pitch`, `fps`) appears in
`/api/health`.

⚠️ **The interaction is a mirror**: the frame is flipped in the backend, once, before
MediaPipe. From there on everything is in screen coordinates and **translations must not be
inverted in TouchDesigner**. Rotations about the vertical axis are the exception: a mirror
reverses the sense of rotation about axes lying in its plane, so *Rotate Y* on `geo1` uses a
negative multiplier.

## Playback control

The audio is played by **TouchDesigner**, not the browser: spectral analysis and beat
detection must stay in sync with the rendering. Commands go over OSC port **9001**.

```bash
curl -X POST http://localhost:8000/api/playback/play
curl -X POST http://localhost:8000/api/playback/pause
curl -X POST http://localhost:8000/api/playback/seek   -H 'Content-Type: application/json' -d '{"value": 42.5}'
curl -X POST http://localhost:8000/api/playback/volume -H 'Content-Type: application/json' -d '{"value": 0.8}'
```

The playback position is **estimated** from the backend clock: TouchDesigner does not report
it back. That is enough, because the mapping works on sections tens of seconds long. The end
of a track is deduced from the known duration plus the estimated position; at the end it
stops and rewinds, it does not advance to the next one.

OSC runs over UDP, so the backend does not fail if TouchDesigner is not open.

## Tools

| Script | Purpose |
|---|---|
| `tools/download_models.py` | downloads the TensorFlow and Face Landmarker models |
| `tools/check_camera.py` | unlocks camera permission on macOS |

Both are part of setting the project up, and both are referenced in the sections above.

