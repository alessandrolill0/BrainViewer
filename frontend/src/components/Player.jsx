import { useMemo } from 'react';
import { clamp01, hash, mmss, orDash, pct, ratioFromEvent } from '../lib/format';

const WAVE_BARS = 104;

/** Track waveform.
 *
 *  Once the analysis is done these are the real peaks, computed by the backend
 *  while decoding the file (analysis.waveform, 104 values 0-1). Before that —
 *  a freshly uploaded track, or a failed analysis — it falls back to a shape
 *  derived from the name: stable per track, but openly not a reading of the
 *  signal. The alternative would be an empty bar. */
function useWave(name, peaks) {
  return useMemo(() => {
    if (Array.isArray(peaks) && peaks.length) {
      // A 6% floor so silences stay visible as a line: at zero the bar
      // disappears and looks like missing data.
      return peaks.map((v) => `${(6 + clamp01(v) * 94).toFixed(0)}%`);
    }
    const seed = hash(name || 'x');
    return Array.from({ length: WAVE_BARS }, (_, k) => {
      const v = Math.abs(
        Math.sin(k * 0.41 + seed * 0.013) * 0.46 +
          Math.sin(k * 0.13 + seed * 0.07) * 0.4 +
          Math.sin(k * 1.7) * 0.14,
      );
      return `${(10 + v * 90).toFixed(0)}%`;
    });
  }, [name, peaks]);
}

export default function Player({ telemetry }) {
  const { current, playing, position, duration, volume, analysis, actions } = telemetry;

  const wave = useWave(current?.title ?? '', analysis?.waveform);
  const progress = duration ? clamp01(position / duration) : 0;
  const hasTrack = Boolean(current);

  const seekTo = (event) => {
    if (!duration) return;
    actions.seek(ratioFromEvent(event) * duration);
  };

  return (
    <footer className="player">
      <div className="player__now">
        <div>
          <div className="player__now-label">NOW PLAYING</div>
          <div className="player__now-name">
            {hasTrack ? (current.title ?? current.filename).toUpperCase() : '— NO SOURCE'}
          </div>
        </div>
        <div className="player__now-meta">
          {hasTrack ? (playing ? 'PLAYING' : 'PAUSED') : 'IDLE'} · OUT → TOUCHDESIGNER
        </div>
      </div>

      <div className="player__main">
        <div className="transport">
          <div className="transport__group">
            <button type="button" className="btn-step" onClick={actions.prev} aria-label="Previous track">
              ◀◀
            </button>
            <button type="button" className="btn-play" onClick={actions.toggle} disabled={!hasTrack}>
              {playing ? '❙❙ PAUSE' : '▶ PLAY'}
            </button>
            <button type="button" className="btn-step" onClick={actions.next} aria-label="Next track">
              ▶▶
            </button>
          </div>

          <div className="clock-big">
            {mmss(position)}
            <span> / {mmss(duration)}</span>
          </div>

          <div className="volume">
            <span className="volume__label">VOL</span>
            <div
              className="volume__hit"
              onClick={(e) => actions.setVolume(ratioFromEvent(e))}
              role="slider"
              tabIndex={0}
              aria-label="Volume"
              aria-valuenow={Math.round(volume * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
              onKeyDown={(e) => {
                if (e.key === 'ArrowRight') actions.setVolume(clamp01(volume + 0.05));
                if (e.key === 'ArrowLeft') actions.setVolume(clamp01(volume - 0.05));
              }}
            >
              <div className="volume__track">
                <div className="volume__fill" style={{ width: pct(volume) }} />
                <div className="volume__knob" style={{ left: pct(volume) }} />
              </div>
            </div>
            <span className="volume__num">{Math.round(volume * 100)}</span>
          </div>
        </div>

        <div>
          <div className="wave" onClick={seekTo}>
            {wave.map((height, k) => (
              <i
                key={k}
                style={{
                  height,
                  // Against the actual bar count, not WAVE_BARS: with the
                  // analysis it is the backend that decides how many.
                  background: k / wave.length <= progress ? 'var(--accent)' : 'var(--bar-idle)',
                }}
              />
            ))}
          </div>
          <div className="seek" onClick={seekTo}>
            <div className="seek__line" />
            <div className="seek__fill" style={{ width: pct(progress) }} />
            <div className="seek__knob" style={{ left: pct(progress) }} />
          </div>
        </div>
      </div>

      <div className="player__analysis">
        <div className="player__analysis-head">
          <span>SPECTRAL ANALYSIS</span>
          <span className="player__analysis-state">
            {analysis.status === 'done'
              ? 'COMPLETE'
              : analysis.status === 'error'
                ? 'FAILED'
                : analysis.status
                  ? 'RUNNING'
                  : '--'}
          </span>
        </div>
        <div className="metrics">
          <div>
            <div className="metric__label">BPM</div>
            <div className="metric__value">{orDash(analysis.bpm, (v) => v.toFixed(0))}</div>
          </div>
          <div>
            <div className="metric__label">RMS</div>
            <div className="metric__value">{orDash(analysis.rms, (v) => v.toFixed(3))}</div>
          </div>
          <div>
            <div className="metric__label">FLUX</div>
            <div className="metric__value">{orDash(analysis.flux, (v) => v.toFixed(2))}</div>
          </div>
        </div>
        <div className="player__out">OUT → TD/OSC</div>
      </div>
    </footer>
  );
}
