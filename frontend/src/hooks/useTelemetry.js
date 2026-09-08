/** The panel's only data entry point.
 *
 *  Everything the UI shows comes from here: a WebSocket for events the backend
 *  pushes (library, playback, analysis progress) and polling of /api/health for
 *  continuous values (lobe activation, head tracking, estimated position).
 *  No component knows about the network.
 *
 *  What the backend cannot know — TouchDesigner render fps and resolution —
 *  stays null and the UI shows `--`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, endpoints } from '../lib/api';
import { REGIONS } from '../lib/regions';
import { hash, stamp } from '../lib/format';

const POLL_MS = 500;
const LOG_MAX = 9;
const RECONNECT_MS = 2000;

const emptyActivation = Object.fromEntries(REGIONS.map((r) => [r.key, 0]));

export function useTelemetry() {
  const [health, setHealth] = useState(null);
  const [tracks, setTracks] = useState([]);
  const [linked, setLinked] = useState(false);
  const [clock, setClock] = useState(stamp());
  const [logs, setLogs] = useState([]);
  const [busy, setBusy] = useState(false);

  const socket = useRef(null);
  const previous = useRef({ present: null, trackId: null, linked: null });

  const log = useCallback((message) => {
    setLogs((rows) => [{ t: stamp(), m: message }, ...rows].slice(0, LOG_MAX));
  }, []);

  /* ── WebSocket: events pushed by the backend ──────────────────────── */

  useEffect(() => {
    let closed = false;
    let retry;

    const connect = () => {
      const ws = new WebSocket(endpoints.ws);
      socket.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'ping' }));
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'library.state') {
          setTracks(msg.tracks ?? []);
        } else if (msg.type === 'playback.ended') {
          // The state itself arrives from the /api/health polling; this is only
          // worth a log line, being the one state change nobody asked for.
          log('TRACK END');
        } else if (msg.type === 'error') {
          log(`ERROR — ${String(msg.message).toUpperCase()}`);
        }
      };

      ws.onclose = () => {
        // Only if this is still the current socket. React mounts twice in dev:
        // the first close arrives once the second is already open, and without
        // this check it would clear the reference to the live one — the panel
        // would keep receiving but could no longer send.
        if (socket.current === ws) socket.current = null;
        if (!closed) retry = setTimeout(connect, RECONNECT_MS);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    // Periodic ping: keeps the socket alive and tells us it still answers.
    const ping = setInterval(() => {
      if (socket.current?.readyState === WebSocket.OPEN) {
        socket.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, 2000);

    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(ping);
      socket.current?.close();
    };
  }, [log]);

  /* ── polling: continuous values ───────────────────────────────────── */

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const data = await api.health();
        if (!alive) return;
        setHealth(data);
        setLinked(true);
      } catch {
        if (alive) {
          setLinked(false);
          setHealth(null);
        }
      }
      if (alive) setClock(stamp());
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    api.library().then((d) => setTracks(d.tracks ?? [])).catch(() => {});
  }, []);

  /* ── log derived from real state changes ──────────────────────────── */

  const playback = health?.playback ?? null;
  const pose = health?.pose ?? null;
  const activation = health?.activation ?? emptyActivation;

  const current = useMemo(
    () => tracks.find((t) => t.track_id === playback?.track_id) ?? null,
    [tracks, playback?.track_id],
  );

  useEffect(() => {
    const prev = previous.current;
    if (prev.linked !== null && prev.linked !== linked) {
      log(linked ? 'LINK RESTORED' : 'LINK LOST — BACKEND UNREACHABLE');
    }
    prev.linked = linked;

    if (pose && prev.present !== pose.present) {
      if (prev.present !== null) log(pose.present ? 'HEAD TRACK: SUBJECT ACQUIRED' : 'HEAD TRACK: NO SUBJECT');
      prev.present = pose.present;
    }
    if (playback?.track_id && prev.trackId !== playback.track_id) {
      if (current) log(`SOURCE LOADED — ${current.title?.toUpperCase() ?? ''}`);
      prev.trackId = playback.track_id;
    }
  }, [linked, pose, playback, current, log]);

  /* ── actions ──────────────────────────────────────────────────────── */

  const guard = useCallback(
    async (fn, message) => {
      setBusy(true);
      try {
        await fn();
        if (message) log(message);
      } catch (error) {
        log(`REJECTED — ${String(error.message).toUpperCase()}`);
      } finally {
        setBusy(false);
      }
    },
    [log],
  );

  const step = useCallback(
    (delta) => {
      if (!tracks.length) return;
      const index = tracks.findIndex((t) => t.track_id === playback?.track_id);
      const target = tracks[(index + delta + tracks.length) % tracks.length];
      const wasPlaying = playback?.playing;
      guard(async () => {
        await api.select(target.track_id);
        if (wasPlaying) await api.play();
      });
    },
    [tracks, playback, guard],
  );

  const actions = useMemo(
    () => ({
      select: (trackId) =>
        guard(async () => {
          await api.select(trackId);
          await api.play();
        }),
      toggle: () =>
        guard(async () => {
          if (playback?.playing) {
            await api.pause();
            log('TRANSPORT: PAUSE');
          } else {
            await api.play();
            log('TRANSPORT: PLAY');
          }
        }),
      next: () => step(1),
      prev: () => step(-1),
      seek: (seconds) => guard(() => api.seek(seconds)),
      setVolume: (value) => guard(() => api.volume(value)),
      togglePose: () =>
        guard(async () => {
          if (pose?.running) {
            await api.poseStop();
            log('WEBCAM: OFFLINE');
          } else {
            await api.poseStart();
            log('WEBCAM: FEED OK');
          }
        }),
      /** Mouse-driven pose. Goes over the WebSocket rather than HTTP: a drag
       *  sends dozens of messages per second. No logging, it would flood the
       *  event log. */
      manualPose: (values) => {
        if (socket.current?.readyState === WebSocket.OPEN) {
          socket.current.send(JSON.stringify({ type: 'pose.manual', ...values }));
        }
      },
      releaseManualPose: () => {
        if (socket.current?.readyState === WebSocket.OPEN) {
          socket.current.send(JSON.stringify({ type: 'pose.manual.release' }));
        }
        log('POSE: MANUAL CONTROL RELEASED');
      },
      calibratePose: () =>
        guard(async () => {
          await api.poseCalibrate();
          log('HEAD TRACK: NEUTRAL POSE SET');
        }),
      /** Deletes the track and its file. The backend broadcasts the new
       *  library, so the list updates on its own. If the deleted track was
       *  playing, playback is stopped first: otherwise the position keeps
       *  running against a file that is no longer there. */
      remove: (trackId, title) =>
        guard(async () => {
          if (playback?.track_id === trackId && playback?.playing) await api.pause();
          await api.remove(trackId);
          log(`REMOVED — ${(title ?? trackId).toUpperCase()}`);
        }),
      upload: async (files) => {
        for (const file of files) {
          try {
            const track = await api.upload(file);
            log(`INGEST — ${(track.title ?? file.name).toUpperCase()}`);
          } catch (error) {
            log(`INGEST FAILED — ${String(error.message).toUpperCase()}`);
          }
        }
      },
    }),
    [guard, playback, pose, step, log],
  );

  /* ── values derived for the UI ────────────────────────────────────── */

  const dominant = useMemo(() => {
    const entries = REGIONS.map((r) => [r.key, activation[r.key] ?? 0]);
    const [key, value] = entries.reduce((a, b) => (b[1] > a[1] ? b : a), entries[0]);
    return { key, value };
  }, [activation]);

  const duration = current?.duration ?? current?.analysis?.duration ?? null;

  return {
    linked,
    clock,
    busy,
    session: `AX-${1000 + (hash('brainviewer') % 900)}`,
    oscPort: health?.osc_streams?.split(':').pop() ?? null,

    tracks,
    current,
    playing: Boolean(playback?.playing),
    position: playback?.position ?? 0,
    duration,
    volume: playback?.volume ?? 1,

    activation,
    dominant,

    pose,
    // Panel-only tool: does not touch the render, and absent until the webcam
    // is on, in which case the component shows '--'.
    expression: health?.expression ?? null,
    poseRunning: Boolean(pose?.running),
    poseSource: pose?.source ?? null,

    analysis: {
      status: current?.analysis_status ?? null,
      bpm: current?.analysis?.bpm ?? null,
      // RMS from the offline analysis: one value per track, not live.
      rms: current?.analysis?.descriptors?.energy_rms ?? null,
      // Track peaks for the player waveform, 0-1. Absent until the analysis is
      // done, in which case the player falls back to a decorative shape.
      waveform: current?.analysis?.waveform ?? null,
    },
    // The 3D render runs in TouchDesigner, which reports neither fps nor format.
    render: { fps: null, resolution: null },

    logs,
    actions,
  };
}
