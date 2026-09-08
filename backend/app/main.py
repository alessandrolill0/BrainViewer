"""BrainViewer — FastAPI backend.

Covers the library, playback commands, offline audio analysis, head tracking and
lobe activation sent to TouchDesigner over OSC.

Frontend --ws/http--> backend --osc--> TouchDesigner
"""

import asyncio
import logging
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (Body, FastAPI, File, HTTPException, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware

from . import analysis as audio_analysis
from . import mapping
from . import signaling
from .library import Library, LibraryError
from .lobes import LobeSender, load_regions
from .osc_client import osc, osc_events
from .playback import PlaybackController, PlaybackError
from .pose import FaceTracker, ManualPose

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOADS = Path(__file__).resolve().parents[1] / 'uploads'

# How often the end of the track is checked. At 0.25 s the worst-case delay is
# imperceptible and the cost is a comparison between two numbers.
WATCH_INTERVAL = 0.25

lobe_sender = LobeSender(osc, load_regions())
playback = PlaybackController(osc_events)
library = Library(UPLOADS)
pose_tracker = FaceTracker(osc)
manual_pose = ManualPose(osc)
#: Relay for the WebRTC handshake between TD and the panel.
signal_hub = signaling.SignalingHub()


class Clients:
    """Connected WebSocket clients, for broadcasting state.

    After every change all clients receive the updated state, so two open tabs
    never contradict each other.
    """

    def __init__(self):
        self._sockets: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        self._sockets.add(ws)

    def discard(self, ws: WebSocket) -> None:
        self._sockets.discard(ws)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_json(message)
            except (RuntimeError, WebSocketDisconnect):
                # Socket died between rounds: drop it and carry on, one fallen
                # client must not block the others.
                self._sockets.discard(ws)


clients = Clients()


def pose_state() -> dict:
    """Current pose, whatever the source.

    The webcam wins: while it runs, manual commands are ignored. Both write to
    the same OSC channels, so letting them coexist means overwriting each other.
    """
    if pose_tracker.running:
        return {**pose_tracker.state(), 'source': 'webcam'}
    return manual_pose.state()


def expression_state() -> dict:
    """Facial expression, only ever from the webcam.

    Mouse-driven pose has no face, so with the webcam off this stays neutral
    and the terms that read it contribute nothing.
    """
    return pose_tracker.expression.state()


def current_activation() -> list[float]:
    """Lobe activation right now, for the LobeSender in `live` mode.

    With no music, or on a track not yet analysed, returns the idle values: the
    brain stays alive and waiting instead of going dark.
    """
    pose = pose_state()
    expression = expression_state()
    if not playback.playing or playback.track_id is None:
        return mapping.idle_vector(pose, expression)
    try:
        track = library.get(playback.track_id)
    except LibraryError:
        return mapping.idle_vector(pose, expression)
    if track.get('analysis_status') != 'done':
        return mapping.idle_vector(pose, expression)
    return mapping.activation(track['analysis'], playback.position(), pose, expression)


def current_synchrony() -> float:
    """How much the firing locks to the beat right now.

    Zero with no music: no beat, nothing to lock onto.
    """
    if not playback.playing or playback.track_id is None:
        return 0.0
    try:
        track = library.get(playback.track_id)
    except LibraryError:
        return 0.0
    if track.get('analysis_status') != 'done':
        return 0.0
    return mapping.synchrony(track['analysis'], playback.position())


async def watchdog() -> None:
    """Watches two things nobody else would report.

    End of track: TouchDesigner reports neither position nor the end, so it is
    deduced from the known duration plus the estimated position. Without this
    the mapping would stay stuck on the last section forever. At the end the
    track stops and rewinds; it does not advance to the next one.

    Stale manual pose: the panel releases it at the end of a drag, but if that
    message never arrives the backend would keep believing someone is in front
    of the webcam.
    """
    while True:
        try:
            if manual_pose.release_if_stale():
                await clients.broadcast({"type": "pose.state", **pose_state()})
            if playback.ended():
                track_id = playback.track_id
                state = playback.finish()
                await clients.broadcast({"type": "playback.ended",
                                         "track_id": track_id})
                await clients.broadcast({"type": "playback.state", **state})
        except Exception:
            # An error here must not kill the watchdog, or end-of-track would
            # silently stop working for the rest of the session.
            logger.exception('Errore nel sorvegliante')
        await asyncio.sleep(WATCH_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    lobe_sender.provider = current_activation
    lobe_sender.sync_provider = current_synchrony
    lobe_sender.start('live')
    watcher = asyncio.create_task(watchdog())
    yield
    watcher.cancel()
    # On shutdown stop everything and zero the lobes, otherwise TD stays on the
    # last value received.
    pose_tracker.stop()
    lobe_sender.start('off')


app = FastAPI(title="BrainViewer backend", version="0.3.0", lifespan=lifespan)

# The Vite dev server runs on another port: without CORS the browser blocks it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def library_state() -> dict:
    return {"type": "library.state", "tracks": library.list()}


async def run_analysis(track_id: str) -> None:
    """Analyse a track off the event loop and update the library.

    Analysis holds the CPU for several seconds; running it in a thread keeps the
    server responsive and playback going.
    """
    try:
        track = library.get(track_id)
    except LibraryError:
        return

    loop = asyncio.get_running_loop()

    def progress(value: float) -> None:
        # Called from the analysis thread: the broadcast goes back to the loop.
        asyncio.run_coroutine_threadsafe(
            clients.broadcast({"type": "analysis.progress",
                               "track_id": track_id, "progress": round(value, 3)}),
            loop)

    library.set_analysis(track_id, 'running')
    await clients.broadcast(library_state())
    try:
        result = await asyncio.to_thread(audio_analysis.analyze,
                                         Path(track['filepath']), progress)
    except Exception as exc:
        logger.exception('Analisi fallita per %s', track_id)
        library.set_analysis(track_id, 'error', error=f'{type(exc).__name__}: {exc}')
    else:
        library.set_analysis(track_id, 'done', analysis=result)
    await clients.broadcast(library_state())


# --- HTTP ---------------------------------------------------------------


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "osc_streams": f"{osc.host}:{osc.port}",
        "osc_events": f"{osc_events.host}:{osc_events.port}",
        "lobes": lobe_sender.state(),
        "playback": playback.state(),
        "activation": dict(zip(mapping.REGION_ORDER, current_activation())),
        "synchrony": current_synchrony(),
        "library_tracks": len(library.list()),
        "mood_models": audio_analysis.models_available(),
        "pose": pose_state(),
        # Panel-only tool: drives nothing in the render and never goes over OSC.
        "expression": expression_state(),
    }


@app.post("/api/pose/start")
async def pose_start():
    """Turn on the webcam and start sending /pose/* to TouchDesigner."""
    pose_tracker.start()
    await asyncio.sleep(1.5)   # time to open the camera and report any error
    state = pose_tracker.state()
    if state.get('error'):
        raise HTTPException(status_code=503, detail=state['error'])
    return state


@app.post("/api/pose/calibrate")
async def pose_calibrate():
    """Set the current pose as neutral (head upright, facing forward)."""
    try:
        return pose_tracker.calibrate()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/pose/stop")
async def pose_stop():
    pose_tracker.stop()
    return pose_tracker.state()


@app.get("/api/library")
async def get_library():
    return {"tracks": library.list()}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Upload a track. The file is streamed to disk, not held in memory."""
    suffix = Path(file.filename or '').suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp, length=1 << 20)
        tmp_path = Path(tmp.name)
    try:
        track = library.add_file(tmp_path, file.filename or 'senza_nome')
    except LibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await clients.broadcast(library_state())
    if not track.get('duplicate'):
        asyncio.create_task(run_analysis(track['track_id']))
    return track


@app.post("/api/tracks/{track_id}/analyze")
async def reanalyze(track_id: str):
    """Re-run the analysis of a track already in the library."""
    try:
        library.get(track_id)
    except LibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    asyncio.create_task(run_analysis(track_id))
    return {"track_id": track_id, "analysis_status": "running"}


@app.delete("/api/tracks/{track_id}")
async def delete_track(track_id: str):
    try:
        track = library.delete(track_id)
    except LibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # If that was the playing track, stop TD rather than leave it on a file
    # that no longer exists.
    if playback.track_id == track_id:
        playback.pause()
    await clients.broadcast(library_state())
    return track


@app.post("/api/library/select/{track_id}")
async def select_track(track_id: str):
    state = _select(track_id)
    await clients.broadcast({"type": "playback.state", **state})
    return state


@app.post("/api/playback/load")
async def playback_load(filepath: str = Body(..., embed=True),
                        track_id: str | None = Body(None, embed=True),
                        duration: float | None = Body(None, embed=True)):
    """Load a file by absolute path, bypassing the library.

    Without `duration` the end of the track is not detected.
    """
    return _guard(playback.load, filepath, track_id, duration)


@app.post("/api/playback/{action}")
async def playback_action(action: str, value: float | None = Body(None, embed=True)):
    """play | pause | seek (value = seconds) | volume (value = 0-1)"""
    if action in ("play", "pause"):
        return _guard(getattr(playback, action))
    if action == "seek":
        if value is None:
            raise HTTPException(400, "seek richiede 'value' in secondi")
        return _guard(playback.seek, value)
    if action == "volume":
        if value is None:
            raise HTTPException(400, "volume richiede 'value' fra 0 e 1")
        return _guard(playback.set_volume, value)
    raise HTTPException(404, f"azione sconosciuta: {action!r}")


@app.post("/api/test/lobes/only/{region}")
async def test_lobe_only(region: str):
    """Light a single region and leave it lit: useful to orient the scene."""
    try:
        lobe_sender.hold_region(region)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return lobe_sender.state()


@app.post("/api/test/lobes/{mode}")
async def test_lobes(mode: str):
    """Test pattern on lobe activation: sweep | pulse | off."""
    try:
        lobe_sender.start(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return lobe_sender.state()


def track_duration(track: dict) -> float | None:
    """Duration in seconds, preferring the one measured by the analysis.

    The analysis actually decodes the file while mutagen reads the header: on a
    VBR mp3 the two can differ by seconds, and the former matches what TD plays.
    """
    analysis = track.get('analysis') or {}
    return analysis.get('duration') or track.get('duration')


def _select(track_id: str) -> dict:
    try:
        track = library.get(track_id)
    except LibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _guard(playback.load, track['filepath'], track_id, track_duration(track))


def _guard(fn, *args):
    try:
        return fn(*args)
    except PlaybackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- WebSocket ----------------------------------------------------------


@app.websocket("/ws/signal/{role}")
async def signaling_endpoint(websocket: WebSocket, role: str):
    """Relay for the WebRTC handshake. Two roles only: `td` and `panel`.

    The video does not pass through here: after negotiation the peers talk
    directly to each other.
    """
    if role not in signaling.ROLES:
        await websocket.close(code=4004)
        return
    await _signaling_session(websocket, role)


async def _signaling_session(websocket: WebSocket, role: str) -> None:
    """Session body, shared by both entry points."""
    await websocket.accept()
    await signal_hub.join(role, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            if not await signal_hub.forward(role, message):
                # Say so rather than swallow it: whoever sent an offer into the
                # void would otherwise wait forever for an answer.
                await websocket.send_json({
                    'type': 'peer.absent', 'role': signal_hub.peer_of(role)})
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError as well: if the client leaves mid-handshake, starlette
        # raises "WebSocket is not connected" instead of a clean disconnect.
        # This happens on every panel reload, because React mounts twice in dev.
        pass
    finally:
        await signal_hub.leave(role, websocket)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("Client WebSocket connesso")
    try:
        await websocket.send_json(library_state())
        while True:
            message = await websocket.receive_json()
            await _handle(websocket, message)
    except WebSocketDisconnect:
        logger.info("Client WebSocket disconnesso")
    finally:
        clients.discard(websocket)


async def _handle(websocket: WebSocket, message: dict) -> None:
    msg_type = message.get("type")

    if msg_type == "ping":
        timestamp = time.time()
        osc.heartbeat(timestamp)
        await websocket.send_json({"type": "pong", "timestamp": timestamp})

    elif msg_type == "library.list":
        await websocket.send_json(library_state())

    elif msg_type in ("library.select", "library.delete"):
        track_id = message.get("track_id", "")
        try:
            if msg_type == "library.select":
                track = library.get(track_id)
                state = playback.load(track['filepath'], track_id,
                                      track_duration(track))
                await clients.broadcast({"type": "playback.state", **state})
            else:
                library.delete(track_id)
                if playback.track_id == track_id:
                    playback.pause()
                await clients.broadcast(library_state())
        except (LibraryError, PlaybackError) as exc:
            await websocket.send_json({
                "type": "error", "code": "library", "message": str(exc),
            })

    elif msg_type == "playback":
        action = message.get("action")
        try:
            if action == "play":
                state = playback.play()
            elif action == "pause":
                state = playback.pause()
            elif action == "seek":
                state = playback.seek(message.get("position", 0.0))
            elif action == "volume":
                state = playback.set_volume(message.get("value", 1.0))
            elif action == "load":
                state = playback.load(message.get("filepath", ""),
                                      message.get("track_id"))
            else:
                raise PlaybackError(f'azione sconosciuta: {action!r}')
        except PlaybackError as exc:
            await websocket.send_json({
                "type": "error", "code": "playback", "message": str(exc),
            })
        else:
            await clients.broadcast({"type": "playback.state", **state})

    elif msg_type == "pose.manual":
        # Arrives in bursts during a drag: no broadcast, no logging. Ignored
        # while the webcam is running.
        if not pose_tracker.running:
            manual_pose.apply(**{k: message.get(k) for k in
                                 ('x', 'y', 'scale', 'turn', 'pitch', 'lean')})

    elif msg_type == "pose.manual.release":
        manual_pose.release()

    elif msg_type == "test.lobes":
        try:
            lobe_sender.start(message.get("mode", "off"))
        except ValueError as exc:
            await websocket.send_json({
                "type": "error", "code": "bad_mode", "message": str(exc),
            })
        else:
            await websocket.send_json({
                "type": "test.lobes.state", **lobe_sender.state(),
            })

    else:
        await websocket.send_json({
            "type": "error",
            "code": "unknown_type",
            "message": f"Tipo di messaggio non gestito: {msg_type!r}",
        })
