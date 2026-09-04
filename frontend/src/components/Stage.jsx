import { useEffect, useRef, useState } from 'react';
import { orDash, signedDeg } from '../lib/format';
import StageFeed from './StageFeed';

/** TouchDesigner render feed, in order of preference.
 *
 *  FEED_SIGNAL -> WebRTC: encoded on the GPU, no pixel readback to the CPU.
 *  FEED_WS     -> JPEG frames pushed over a WebSocket.
 *  FEED_URL    -> JPEG polled at FEED_FPS per second.
 *
 *  All empty leaves the placeholder. The two JPEG routes stay as a fallback and
 *  as diagnostics: FEED_URL opens in any browser. */
const FEED_SIGNAL = import.meta.env.VITE_TD_SIGNAL ?? '';
const FEED_URL = import.meta.env.VITE_TD_FEED ?? '';
const FEED_WS = import.meta.env.VITE_TD_FEED_WS ?? '';
const FEED_FPS = Number(import.meta.env.VITE_TD_FEED_FPS ?? 25);

const BARS = 26;
const MOTION_THRESHOLD = 0.55;

/** Estimated distance from the webcam. `scale` is the apparent size of the
 *  face, mapped linearly onto the two typical working distances. A derived
 *  estimate, not an optical measurement, but the only depth cue face tracking
 *  provides. */
const distanceCm = (scale) => 140 - scale * 100;

const motionLabel = (energy) => (energy > 0.66 ? 'HIGH' : energy > 0.33 ? 'MEDIUM' : 'LOW');

/** How much a drag across the full width of the box rotates the model. At 5.0
 *  a flick of the wrist covers the whole arc. This is the knob to touch if it
 *  feels too or too little responsive. */
const DRAG_GAIN = 5.0;

/** How much silence marks the end of a wheel gesture. */
const WHEEL_RELEASE_MS = 400;

/** Degrees corresponding to `turn = 1` and `pitch = 1`.
 *  These must match the multipliers in the Rotate expressions of `geo1` in
 *  TouchDesigner, or the HUD reports an angle the brain does not have. It is
 *  the one place where the panel repeats a number that lives in TD. */
const YAW_DEG = 90;
const PITCH_DEG = 30;
const ROLL_DEG = 25;

export default function Stage({ telemetry }) {
  const { pose, poseRunning, poseSource, actions } = telemetry;
  // The stream itself reports the resolution: the only fact about the render
  // available without a return channel from TouchDesigner.
  const [feed, setFeed] = useState({
    live: false, width: null, height: null, fps: null, transport: null,
  });
  const manual = poseSource === 'manual';
  const live = Boolean(pose?.present);
  const energy = live && !manual ? (pose.energy ?? 0) : 0;

  // The mouse only drives while the webcam is off: both write to the same OSC
  // channels and would overwrite each other.
  const canDrag = !poseRunning;

  // The gesture is followed with listeners on `window`, attached inside the
  // handler rather than from an effect: an effect would run after React's
  // redraw, and in a fast drag the first moves would arrive before anyone was
  // listening. This also works if the cursor leaves the box mid-gesture.
  const latest = useRef({ actions, pose });
  latest.current = { actions, pose };

  const onPointerDown = (event) => {
    if (!canDrag) return;
    const box = event.currentTarget.getBoundingClientRect();
    const start = {
      x: event.clientX,
      y: event.clientY,
      turn: pose?.turn ?? 0,
      pitch: pose?.pitch ?? 0,
      width: box.width || 1,
      height: box.height || 1,
    };

    const move = (moveEvent) => {
      latest.current.actions.manualPose({
        turn: start.turn + ((moveEvent.clientX - start.x) / start.width) * DRAG_GAIN,
        // Dragging down tilts the model down: the sign was inverted relative
        // to how one expects to handle an object.
        pitch: start.pitch + ((moveEvent.clientY - start.y) / start.height) * DRAG_GAIN,
      });
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      // Without this the backend keeps believing someone is in front of the
      // webcam and the occipital lobe never goes dark again. The orientation
      // stays: releasing only clears presence.
      latest.current.actions.releaseManualPose();
    };

    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
  };

  // The wheel has no "pointerup": the end of the gesture is inferred from
  // silence. Without it, one scroll would leave presence switched on.
  const wheelRelease = useRef(null);

  const onWheel = (event) => {
    if (!canDrag) return;
    latest.current.actions.manualPose({
      scale: (latest.current.pose?.scale ?? 0.5) - event.deltaY * 0.0015,
    });
    clearTimeout(wheelRelease.current);
    wheelRelease.current = setTimeout(
      () => latest.current.actions.releaseManualPose(),
      WHEEL_RELEASE_MS,
    );
  };

  useEffect(() => () => clearTimeout(wheelRelease.current), []);

  // The bars draw the movement profile at the current amplitude: with no
  // history from the backend, the envelope is a deterministic function of
  // energy and index, not a made-up value pretending to be a sample.
  const bars = Array.from({ length: BARS }, (_, k) => {
    const shape = Math.abs(Math.sin(k * 0.5 + energy * 6));
    const value = live ? energy * (0.45 + 0.55 * shape) : 0;
    return { h: `${(12 + value * 88).toFixed(0)}%`, on: value > MOTION_THRESHOLD };
  });

  return (
    <main className="stage">
      <div className="stage__core" />
      <div
        className={`stage__frame${canDrag ? ' stage__frame--grab' : ''}`}
        onPointerDown={onPointerDown}
        onWheel={onWheel}
      >
        <div className="stage__feed">
          <StageFeed
            url={FEED_URL}
            wsUrl={FEED_WS}
            signalUrl={FEED_SIGNAL}
            fps={FEED_FPS}
            onStatus={setFeed}
          />
        </div>
        {!feed.live && (
          <div className="stage__feed-note">AREA RISERVATA AL MODELLO 3D — IN ATTESA DI TOUCHDESIGNER</div>
        )}
        {/* Crosshairs and hatching say "the model goes here" while the box is
            empty. With the render inside they would be decoration on top of the
            artwork, so they disappear. Corners and scrims stay: the first
            frame it, the second keep the labels readable on a bright render. */}
        {!feed.live && <div className="stage__hatch" />}
        <div className="stage__scrim stage__scrim--top" />
        <div className="stage__scrim stage__scrim--bottom" />
        <div className="stage__corner stage__corner--tl" />
        <div className="stage__corner stage__corner--tr" />
        <div className="stage__corner stage__corner--bl" />
        <div className="stage__corner stage__corner--br" />
        {!feed.live && (
          <>
            <div className="stage__cross-v" />
            <div className="stage__cross-h" />
          </>
        )}

        <div className="stage__labels">
          <span className="stage__labels-left">
            <span style={{ color: 'var(--accent)' }}>TD // RENDER 3D</span>
            <span>CERVELLO — VISTA PRINCIPALE</span>
          </span>
          <span style={{ whiteSpace: 'nowrap' }}>
            {feed.live && feed.fps ? `${feed.fps} FPS` : '-- FPS'} ·{' '}
            {feed.width ? `${feed.width}×${feed.height}` : '--'}
          </span>
        </div>

        <div className="stage__spacer" />

        <div className="stage__hud">
          <div className="hud-group">
            <div>
              <div className="hud__label">HEAD YAW</div>
              <div className="hud__value hud__value--on">
                {live ? signedDeg(pose.turn * YAW_DEG) : '--'}
              </div>
            </div>
            <div>
              <div className="hud__label">PITCH</div>
              <div className="hud__value">{live ? signedDeg(pose.pitch * PITCH_DEG) : '--'}</div>
            </div>
            <div>
              <div className="hud__label">ROLL</div>
              <div className="hud__value">{live ? signedDeg(pose.lean * ROLL_DEG) : '--'}</div>
            </div>
            <div>
              <div className="hud__label">DIST</div>
              <div className="hud__value">
                {live ? `${distanceCm(pose.scale).toFixed(0)} cm` : '--'}
              </div>
            </div>
          </div>

          <div className="motion">
            <div className="motion__head">
              <span>MOTION INPUT</span>
              <span className="motion__level">
                {manual ? 'MOUSE' : live ? motionLabel(energy) : 'NO SUBJECT'}
              </span>
            </div>
            <div className="motion__bars">
              {bars.map((bar, k) => (
                <i
                  key={k}
                  style={{ height: bar.h, background: bar.on ? 'var(--accent)' : 'var(--bar-idle-alt)' }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
