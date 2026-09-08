import { useEffect, useRef, useState } from 'react';
import { mmss, pad } from '../lib/format';
import Subject from './Subject';

const AUDIO_RE = /\.(wav|mp3|flac|aiff?|ogg|m4a)$/i;
const accepted = (file) => file.type.startsWith('audio') || AUDIO_RE.test(file.name);

/** How long a delete stays armed before going back to being harmless. */
const ARM_MS = 4000;

/** Source library plus drop zone. The drop zone is the only dashed border on
 *  the screen, which is what makes it recognisable. */
export default function Library({ telemetry }) {
  const { tracks, current, playing, actions } = telemetry;
  const [over, setOver] = useState(false);
  const [armed, setArmed] = useState(null);
  const input = useRef(null);
  const armTimer = useRef(null);

  // The decision reads the ref, not the state: two clicks in the same frame
  // would both see the pre-render value and the second would re-arm instead of
  // confirming. The state exists only to redraw the button.
  const armedRef = useRef(null);
  const setArm = (trackId) => {
    clearTimeout(armTimer.current);
    armedRef.current = trackId;
    setArmed(trackId);
    if (trackId) armTimer.current = setTimeout(() => setArm(null), ARM_MS);
  };
  useEffect(() => () => clearTimeout(armTimer.current), []);

  const send = (list) => {
    const files = Array.from(list).filter(accepted);
    if (files.length) actions.upload(files);
  };

  return (
    <aside className="library">
      {/* Above the library, not inside it: the library is what the visitor
          controls, this is what the installation sees. */}
      <Subject expression={telemetry.expression} poseRunning={telemetry.poseRunning} />

      <div className="library__head">
        <span className="panel-label">LIBRARY / SOURCES</span>
        <span className="library__count">{tracks.length} SOURCES</span>
      </div>

      <div className="library__list">
        {tracks.map((track, index) => {
          const on = track.track_id === current?.track_id;
          const processing = track.analysis_status !== 'done';
          const arming = armed === track.track_id;
          const title = track.title ?? track.filename ?? '';
          return (
            <div key={track.track_id} className={`track${on ? ' track--on' : ''}`}>
              <button
                type="button"
                className="track__pick"
                onClick={() => actions.select(track.track_id)}
              >
                <span className="track__num">{on ? (playing ? '▶' : '❙❙') : pad(index + 1)}</span>
                <span style={{ minWidth: 0 }}>
                  <span className="track__name" style={{ display: 'block' }}>
                    {title.toUpperCase()}
                  </span>
                  <span className="track__meta" style={{ display: 'block' }}>
                    {mmss(track.duration)} · {track.analysis_status === 'error' ? 'ERROR' : 'LOCAL'}
                  </span>
                </span>
              </button>
              <span className="track__end">
              <span className={`track__badge${processing ? ' track__badge--busy' : ''}`}>
                {track.analysis_status === 'done'
                  ? 'ANALYZED'
                  : track.analysis_status === 'error'
                    ? 'FAILED'
                    : 'PROC...'}
              </span>
              {/* Two steps rather than a dialog: deleting removes the file from
                  disk, and the design has no modals. The armed state expires on
                  its own, so a forgotten click does not stay dangerous. */}
              <button
                type="button"
                className={`track__del${arming ? ' track__del--armed' : ''}`}
                title={arming ? `Confirm deleting ${title}` : `Delete ${title}`}
                aria-label={arming ? `Confirm deleting ${title}` : `Delete ${title}`}
                onClick={() => {
                  if (armedRef.current === track.track_id) {
                    setArm(null);
                    actions.remove(track.track_id, title);
                  } else {
                    setArm(track.track_id);
                  }
                }}
              >
                {arming ? 'SURE?' : '×'}
              </button>
              </span>
            </div>
          );
        })}
      </div>

      <button
        type="button"
        className={`dropzone${over ? ' dropzone--over' : ''}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!over) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          send(e.dataTransfer.files);
        }}
      >
        <span className="dropzone__cta" style={{ display: 'block' }}>+ UPLOAD AUDIO</span>
        <span className="dropzone__hint" style={{ display: 'block' }}>DROP HERE · WAV / MP3 / FLAC</span>
        <input
          ref={input}
          type="file"
          accept="audio/*"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            send(e.target.files);
            e.target.value = '';
          }}
        />
      </button>
    </aside>
  );
}
