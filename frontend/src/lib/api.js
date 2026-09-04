/** The only contact point with the backend.
 *
 *  No component talks to the network directly: everything goes through here and
 *  useTelemetry, so changing transport does not touch the UI.
 */

const HTTP = import.meta.env.VITE_BACKEND_HTTP ?? 'http://localhost:8000';
const WS = import.meta.env.VITE_BACKEND_WS ?? 'ws://localhost:8000/ws';

export const endpoints = { http: HTTP, ws: WS };

async function request(path, options = {}) {
  const response = await fetch(HTTP + path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* body was not JSON: keep the status code */
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

export const api = {
  health: () => request('/api/health'),
  library: () => request('/api/library'),

  select: (trackId) => request(`/api/library/select/${trackId}`, { method: 'POST' }),
  remove: (trackId) => request(`/api/tracks/${trackId}`, { method: 'DELETE' }),
  analyze: (trackId) => request(`/api/tracks/${trackId}/analyze`, { method: 'POST' }),

  play: () => request('/api/playback/play', { method: 'POST', body: '{}' }),
  pause: () => request('/api/playback/pause', { method: 'POST', body: '{}' }),
  seek: (seconds) =>
    request('/api/playback/seek', { method: 'POST', body: JSON.stringify({ value: seconds }) }),
  volume: (value) =>
    request('/api/playback/volume', { method: 'POST', body: JSON.stringify({ value }) }),

  poseStart: () => request('/api/pose/start', { method: 'POST', body: '{}' }),
  poseStop: () => request('/api/pose/stop', { method: 'POST', body: '{}' }),
  poseCalibrate: () => request('/api/pose/calibrate', { method: 'POST', body: '{}' }),

  /** Upload an audio file. The backend replies with the created track. */
  upload: async (file) => {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch(`${HTTP}/api/upload`, { method: 'POST', body: form });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        detail = (await response.json())?.detail ?? detail;
      } catch {
        /* ignored */
      }
      throw new Error(detail);
    }
    return response.json();
  },
};
