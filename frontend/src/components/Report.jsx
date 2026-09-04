import { useState } from 'react';
import { REGIONS, REGION_BY_KEY } from '../lib/regions';
import Wireframe from './Wireframe';

/** Report, selectable sector map, wireframe and event log.
 *  The values are the real activation computed by backend/app/mapping.py. */
export default function Report({ telemetry }) {
  const { activation, dominant, logs } = telemetry;

  // No selection means the report follows the dominant area. Selecting a
  // sector pins it; clicking again resumes following. The badge always says
  // which mode is active, otherwise a frozen value looks like a fault.
  const [selected, setSelected] = useState(null);
  const toggle = (key) => setSelected((prev) => (prev === key ? null : key));

  const shownKey = selected ?? dominant.key;
  const region = REGION_BY_KEY[shownKey] ?? REGIONS[0];
  const percent = Math.round((activation[shownKey] ?? 0) * 100);

  return (
    <aside className="report">
      <div className="report__block">
        <div className="report__head">
          <span>REPORT / {selected ? 'SELECTED AREA' : 'DOMINANT AREA'}</span>
          {selected ? (
            <button type="button" className="report__release" onClick={() => setSelected(null)}>
              RELEASE
            </button>
          ) : (
            <span className="report__live">
              <span className="blip blip--sm" />
              LIVE
            </span>
          )}
        </div>
        <div className="report__name">{region.name}</div>
        <div className="report__meta">
          {region.code} · ACTIVATION {percent}% · {region.trigger}
        </div>
        <div className="report__desc">{region.desc}</div>
        <div className="report__bar">
          <i style={{ width: `${percent}%` }} />
        </div>
        <div className="report__scale">
          <span>0</span>
          <span>THRESHOLD 60</span>
          <span>100</span>
        </div>
      </div>

      <div className="report__block">
        <div className="panel-label panel-label--tight">SECTOR MAP</div>
        <div className="sectors">
          {REGIONS.map((r) => {
            const on = r.key === shownKey;
            return (
              <button
                type="button"
                key={r.key}
                className={`sector${on ? ' sector--on' : ''}${
                  selected === r.key ? ' sector--held' : ''}`}
                onClick={() => toggle(r.key)}
                aria-pressed={selected === r.key}
              >
                <div className="sector__top">
                  <span>{r.code}</span>
                  <span>{Math.round((activation[r.key] ?? 0) * 100)}</span>
                </div>
                <div className="sector__name">{r.short}</div>
              </button>
            );
          })}
        </div>

        <Wireframe
          activation={activation}
          selected={selected}
          dominant={dominant.key}
          onSelect={toggle}
        />
      </div>

      <div className="log">
        <div className="log__head">
          <span>EVENT LOG</span>
          <span className="log__count">{logs.length} EVENTS</span>
        </div>
        <div className="log__rows">
          {logs.map((row, k) => (
            <div key={`${row.t}-${k}`} className={`log__row${k === 0 ? ' log__row--last' : ''}`}>
              <span className="log__time">{row.t}</span>
              <span>{row.m}</span>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
