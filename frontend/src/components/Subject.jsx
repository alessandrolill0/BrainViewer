import { orDash } from '../lib/format';

/** SUBJECT / STATE — facial expression of whoever is in front of the webcam.
 *
 *  Two readings, shown as two different things because they are: valence and
 *  arousal are marked as estimated, since inferring emotion from a face is
 *  scientifically contested; the actions (smile, brow, jaw) are observed
 *  quantities and carry no qualifier.
 *
 *  No colours: negative valence reads from the position relative to the centre,
 *  not from a red.
 */
export default function Subject({ expression, poseRunning }) {
  const live = poseRunning && expression?.present;
  const available = expression?.available !== false;

  // Valence is centred: 0.5 is neutral and the bar grows from there either way.
  // A bar starting at zero would say a still face is "slightly positive", which
  // is not the same as neutral.
  const valence = expression?.valence ?? 0.5;
  const offset = Math.min(valence, 0.5) * 100;
  const width = Math.abs(valence - 0.5) * 100;
  const arousal = (expression?.arousal ?? 0) * 100;
  const strong = (expression?.mood_strength ?? 0) >= 0.35;

  const azioni = [
    ['SMILE', expression?.smile],
    ['BROW', expression?.brow],
    ['JAW', expression?.jaw],
  ];

  return (
    <section className="subject">
      <div className="subject__head">
        <span>SUBJECT / STATE</span>
        {live ? (
          <span className="subject__live">
            <span className="blip blip--sm" />
            LIVE
          </span>
        ) : (
          <span className="subject__off">{available ? 'NO SUBJECT' : 'MODEL MISSING'}</span>
        )}
      </div>

      <div className="subject__row">
        <span className="subject__label">VALENCE</span>
        <div className="subject__track subject__track--signed">
          <i className="subject__center" />
          {live && <i className="subject__fill" style={{ left: `${offset}%`, width: `${width}%` }} />}
        </div>
        <span className="subject__num">
          {live ? (valence >= 0.5 ? '+' : '−') + Math.abs(valence - 0.5).toFixed(2).slice(1) : '--'}
        </span>
      </div>

      <div className="subject__row">
        <span className="subject__label">AROUSAL</span>
        <div className="subject__track">
          {live && <i className="subject__fill" style={{ left: 0, width: `${arousal}%` }} />}
        </div>
        <span className="subject__num">{live ? (arousal / 100).toFixed(2) : '--'}</span>
      </div>

      <div className="subject__note">ESTIMATED FROM FACIAL ACTIONS</div>

      {/* Mood is the circumplex quadrant the two bars fall into, so it is an
          estimate of an estimate. A mood_strength below 0.35 means the face is
          close to neutral, and the word would otherwise appear with the same
          confidence as a clear reading. */}
      <div className="subject__mood">
        <span className="subject__label">MOOD</span>
        <span className={`subject__mood-value${live && strong ? ' subject__mood-value--on' : ''}`}>
          {live ? expression.mood : '--'}
        </span>
        <span className="subject__mood-hint">
          {live && !strong && expression.mood !== 'NEUTRAL' ? 'WEAK READING' : ''}
        </span>
      </div>

      <div className="subject__actions">
        {azioni.map(([nome, valore]) => (
          <div key={nome} className="subject__action">
            <span>{nome}</span>
            <span className="subject__action-num">
              {live ? orDash(valore, (v) => v.toFixed(2)) : '--'}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
