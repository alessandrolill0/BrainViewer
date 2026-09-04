import { REGIONS } from '../lib/regions';
import { COLORS, OUTLINES, VIEW_BOX } from '../lib/wireframe';

/** Anatomical wireframe, side view.
 *
 *  The outlines are not drawn but measured from the real region OBJs by
 *  tools/make_wireframe.py, the same geometry TouchDesigner instances. Rerun the
 *  script after a model change and the panel stays aligned.
 *
 *  The regions use the same palette as the neurons, read from palette.tsv by
 *  that script, so the drawing is a legend for the render rather than a second
 *  language. The rest of the panel stays monochrome.
 *
 *  Clicking a region selects it, exactly like the sector map.
 */
export default function Wireframe({ activation, selected, dominant, onSelect }) {
  return (
    <div className="anatomy">
      <svg className="anatomy__svg" viewBox={VIEW_BOX} role="img"
           aria-label="Anatomical wireframe: brain profile by region">
        {REGIONS.filter((r) => OUTLINES[r.key]).map((r) => {
          const on = r.key === selected;
          // With no selection the wireframe follows the dominant area, so the
          // panel is never mute about where the brain is working.
          const live = !selected && r.key === dominant;
          const value = activation[r.key] ?? 0;
          return (
            <path
              key={r.key}
              d={OUTLINES[r.key]}
              className={`anatomy__region${on || live ? ' anatomy__region--on' : ''}`}
              style={{
                stroke: COLORS[r.key],
                // Colour identifies the region, opacity says how active it is:
                // two facts on two channels, instead of a hue that shifts until
                // it is no longer recognisable.
                opacity: on ? 1 : 0.3 + value * 0.55,
                strokeWidth: on ? 6 : live ? 4 : 3,
              }}
              onClick={() => onSelect(r.key)}
            >
              <title>{`${r.name} — ${Math.round(value * 100)}%`}</title>
            </path>
          );
        })}
      </svg>
    </div>
  );
}
