import { useEffect, useState } from 'react';
import { orDash } from '../lib/format';

/** Fullscreen. Safari still exposes the prefixed API, so the standard one is
 *  tried first and the webkit variant second. */
function useFullscreen() {
  const [on, setOn] = useState(false);

  useEffect(() => {
    const sync = () => setOn(Boolean(document.fullscreenElement || document.webkitFullscreenElement));
    document.addEventListener('fullscreenchange', sync);
    document.addEventListener('webkitfullscreenchange', sync);
    sync();
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      document.removeEventListener('webkitfullscreenchange', sync);
    };
  }, []);

  const toggle = () => {
    const root = document.documentElement;
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      (document.exitFullscreen ?? document.webkitExitFullscreen)?.call(document);
    } else {
      (root.requestFullscreen ?? root.webkitRequestFullscreen)?.call(root);
    }
  };

  return [on, toggle];
}

/** Status bar: everything that says whether the installation is alive.
 *  Degraded states are spelled out (SIGNAL LOST, NO SUBJECT, OFFLINE) rather
 *  than disappearing. */
export default function StatusBar({ telemetry }) {
  const { linked, clock, session, oscPort, pose, poseRunning, actions } = telemetry;

  const tracking = !poseRunning ? 'OFFLINE' : pose?.present ? 'ACTIVE' : 'NO SUBJECT';
  const [fullscreen, toggleFullscreen] = useFullscreen();

  return (
    <header className="status">
      <div className="status__cell status__cell--brand">
        <span className="blip" />
        BRAIN VIEWER
      </div>
      <div className="status__cell">
        LINK<span className={linked ? 'status__on' : 'status__num'}>{linked ? 'STABLE' : 'SIGNAL LOST'}</span>
      </div>
      {/* A click here sets the current pose as "head upright": how far the nose
          sits below the eye line depends on the camera angle, and measuring
          beats guessing at every new setup. */}
      <button
        type="button"
        className="status__cell status__cell--action"
        onClick={actions.calibratePose}
        disabled={tracking !== 'ACTIVE'}
        title="Sit upright in front of the webcam and click: sets the neutral pose"
      >
        TRACKING
        <span className={tracking === 'ACTIVE' ? 'status__on' : 'status__num'}>{tracking}</span>
      </button>
      <div className="status__cell">
        TD-OSC<span className="status__num">{oscPort ? `:${oscPort}` : '--'}</span>
      </div>
      {/* The cell is also the control: the design had no button for tracking,
          and adding one would have introduced an element outside the system.
          Making an existing indicator interactive stays within the language. */}
      <button
        type="button"
        className="status__cell status__cell--action"
        onClick={actions.togglePose}
        title={poseRunning ? 'Stop face tracking' : 'Start face tracking'}
      >
        WEBCAM
        <span className={poseRunning ? 'status__on' : 'status__num'}>
          {poseRunning ? 'FEED OK' : 'OFFLINE'}
        </span>
      </button>
      {/* Fullscreen: same shape as the other control cells, label plus state
          value. No new element, no icon. */}
      <button
        type="button"
        className="status__cell status__cell--action"
        onClick={toggleFullscreen}
        title={fullscreen ? 'Leave full screen' : 'Go full screen'}
      >
        SCREEN
        <span className={fullscreen ? 'status__on' : 'status__num'}>
          {fullscreen ? 'FULLSCREEN' : 'WINDOWED'}
        </span>
      </button>

      <div className="status__cell status__cell--right">
        <span>SESSION {session}</span>
        <span className="status__clock">{clock}</span>
      </div>
    </header>
  );
}
