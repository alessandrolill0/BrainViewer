import { useRef, useState } from 'react';
import { mmss, pad } from '../lib/format';
import Subject from './Subject';

const AUDIO_RE = /\.(wav|mp3|flac|aiff?|ogg|m4a)$/i;
const accepted = (file) => file.type.startsWith('audio') || AUDIO_RE.test(file.name);

/** Source library plus drop zone. The drop zone is the only dashed border on
 *  the screen, which is what makes it recognisable. */
export default function Library({ telemetry }) {
  const { tracks, current, playing, actions } = telemetry;
  const [over, setOver] = useState(false);
  const input = useRef(null);

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
          return (
            <button
              key={track.track_id}
              type="button"
              className={`track${on ? ' track--on' : ''}`}
              onClick={() => actions.select(track.track_id)}
            >
              <span className="track__num">{on ? (playing ? '▶' : '❙❙') : pad(index + 1)}</span>
              <span style={{ minWidth: 0 }}>
                <span className="track__name" style={{ display: 'block' }}>
                  {(track.title ?? track.filename ?? '').toUpperCase()}
                </span>
                <span className="track__meta" style={{ display: 'block' }}>
                  {mmss(track.duration)} · {track.analysis_status === 'error' ? 'ERROR' : 'LOCAL'}
                </span>
              </span>
              <span className={`track__badge${processing ? ' track__badge--busy' : ''}`}>
                {track.analysis_status === 'done'
                  ? 'ANALYZED'
                  : track.analysis_status === 'error'
                    ? 'FAILED'
                    : 'PROC...'}
              </span>
            </button>
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
