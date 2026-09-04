/** Shared formatting. The panel reads like a clinical report: no friendly
 *  abbreviations, always two digits, explicit placeholders. */

export const pad = (n) => String(n).padStart(2, '0');

/** mm:ss, or `--:--` when the value is missing. */
export const mmss = (seconds) =>
  Number.isFinite(seconds) && seconds >= 0
    ? `${pad(Math.floor(seconds / 60))}:${pad(Math.floor(seconds % 60))}`
    : '--:--';

export const stamp = (date = new Date()) =>
  [date.getHours(), date.getMinutes(), date.getSeconds()].map(pad).join(':');

/** Stable hash of a string: the waveform must stay identical between
 *  renders, otherwise it "breathes" on every state update. */
export const hash = (text) => {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) % 99991;
  return h;
};

export const clamp01 = (v) => Math.min(1, Math.max(0, v));

/** Horizontal fraction of a click on the element: used by seek and volume. */
export const ratioFromEvent = (event) => {
  const rect = event.currentTarget.getBoundingClientRect();
  return clamp01((event.clientX - rect.left) / rect.width);
};

export const pct = (v) => `${(clamp01(v) * 100).toFixed(2)}%`;

/** Numeric value or placeholder, keeping the format. */
export const orDash = (value, format) =>
  value === null || value === undefined || Number.isNaN(value) ? '--' : format(value);

export const signedDeg = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}°`;
