import { useEffect, useRef, useState } from 'react';
import { connectFeed } from '../lib/webrtc';

/** TouchDesigner render feed, with three strategies in order of preference.
 *
 *  WebRTC (best, when `signalUrl` is configured): the video is encoded on the
 *  GPU and comes straight from TouchDesigner. The only route that does not make
 *  TD read the pixels back to the CPU, which costs ~70 ms per frame at 720p.
 *
 *  WebSocket: TouchDesigner pushes JPEG frames at its own pace, decoded with
 *  createImageBitmap off the main thread and drawn on a canvas.
 *
 *  Polling: one HTTP request per frame. Useful as a fallback and as
 *  diagnostics, since the address opens in any browser.
 *
 *  The reported frame rate is always the measured one, not the requested one.
 */

const RETRY_MS = 3000;
const POOL = 2;
/** If the WebSocket connects but no frame arrives within this time, fall back
 *  to HTTP polling: an empty panel says nothing, a choppy image does. */
const FALLBACK_MS = 5000;

/** Draws covering the box, like `object-fit: cover`. That property does not
 *  apply to a canvas, so the crop has to be computed. */
function drawCover(context, source, width, height) {
  const scale = Math.max(width / source.width, height / source.height);
  const w = source.width * scale;
  const h = source.height * scale;
  context.drawImage(source, (width - w) / 2, (height - h) / 2, w, h);
}

export default function StageFeed({ url, wsUrl, signalUrl, fps = 25, onStatus }) {
  const [live, setLive] = useState(false);
  const [rtcFailed, setRtcFailed] = useState(false);
  const video = useRef(null);
  // WebRTC wins; if it is not configured or fails to connect within the grace
  // period, fall back to JPEG rather than leave an empty box.
  const rtcActive = Boolean(signalUrl) && !rtcFailed;
  const [fellBack, setFellBack] = useState(false);
  const [frame, setFrame] = useState(null);      // polling mode only
  const wsActive = Boolean(wsUrl) && !fellBack && !rtcActive;
  const canvas = useRef(null);
  const timer = useRef(null);
  const pool = useRef([]);
  const slot = useRef(0);
  // Timestamps of recent frames, to measure the real rate. In a ref and not in
  // an object rebuilt on every render: a changing identity would land in the
  // effect dependencies and reconnect the WebSocket on every redraw.
  const marks = useRef([]);

  const measure = () => {
    const now = performance.now();
    marks.current = [...marks.current, now].filter((t) => now - t < 2000);
    return marks.current.length > 1
      ? Math.round(((marks.current.length - 1) * 1000) / (now - marks.current[0]))
      : null;
  };

  /* ── WebRTC ────────────────────────────────────────────────────────── */

  useEffect(() => setRtcFailed(false), [signalUrl]);

  useEffect(() => {
    if (!rtcActive || !video.current) return undefined;

    const element = video.current;
    // The fallback only fires if negotiation stops progressing. Re-arming it on
    // every step avoids dropping to JPEG while the handshake is succeeding, just
    // because it takes a few seconds longer than expected.
    let fallback = null;
    const armaRipiego = () => {
      clearTimeout(fallback);
      fallback = setTimeout(() => setRtcFailed(true), FALLBACK_MS);
    };
    armaRipiego();

    const stop = connectFeed(signalUrl, element, ({ phase, detail, fps, width, height }) => {
      if (phase === 'negotiating' || phase === 'connecting') armaRipiego();

      if (phase === 'live') {
        clearTimeout(fallback);
        fallback = null;
        setLive(true);
        // fps and resolution come from the stream statistics: frames actually
        // decoded, not requested.
        onStatus?.({ live: true, width, height, fps, transport: 'WEBRTC' });
      } else if (phase === 'lost' || phase === 'offline' || phase === 'waiting') {
        // Only these phases switch the feed off. `connecting` and `negotiating`
        // concern signaling, which reconnects while the video keeps running:
        // treating them as a loss put a "waiting" label over a live render.
        if (detail !== 'in attesa di TD') {
          setLive(false);
          onStatus?.({ live: false, width: null, height: null, fps: null, detail });
        }
      }
    });

    return () => {
      clearTimeout(fallback);
      stop();
    };
  }, [rtcActive, signalUrl, onStatus]);

  /* ── WebSocket ─────────────────────────────────────────────────────── */

  useEffect(() => setFellBack(false), [wsUrl]);

  useEffect(() => {
    if (!wsActive) return undefined;

    let alive = true;
    let socket;
    let retry;
    // Silence generates no events: without a timer, an open WebSocket sending
    // nothing would leave the box empty forever.
    let watchdog = url ? setTimeout(() => alive && setFellBack(true), FALLBACK_MS) : null;
    let pending = false;   // one frame at a time: if decoding is slower than
                           // arrival, older frames are dropped

    const connect = () => {
      socket = new WebSocket(wsUrl);
      socket.binaryType = 'blob';

      socket.onmessage = async (event) => {
        if (!alive || pending || !(event.data instanceof Blob)) return;
        pending = true;
        clearTimeout(watchdog);
        watchdog = null;
        try {
          const bitmap = await createImageBitmap(event.data);
          if (!alive) return bitmap.close();
          const element = canvas.current;
          if (element) {
            const box = element.getBoundingClientRect();
            const dpr = Math.min(2, window.devicePixelRatio || 1);
            const width = Math.round(box.width * dpr);
            const height = Math.round(box.height * dpr);
            if (element.width !== width || element.height !== height) {
              element.width = width;
              element.height = height;
            }
            drawCover(element.getContext('2d'), bitmap, width, height);
          }
          setLive(true);
          onStatus?.({
            live: true,
            width: bitmap.width,
            height: bitmap.height,
            fps: measure(),
            transport: 'WEBSOCKET',
          });
          bitmap.close();
        } catch {
          /* corrupt frame: wait for the next one */
        } finally {
          pending = false;
        }
      };

      socket.onclose = () => {
        if (!alive) return;
        setLive(false);
        marks.current = [];
        onStatus?.({ live: false, width: null, height: null, fps: null });
        retry = setTimeout(connect, RETRY_MS);
      };
      socket.onerror = () => socket.close();
    };

    connect();
    return () => {
      alive = false;
      clearTimeout(retry);
      clearTimeout(watchdog);
      socket?.close();
    };
  }, [wsActive, url, onStatus]);

  /* ── HTTP polling, when the WebSocket is not configured ───────────── */

  useEffect(() => {
    // `rtcActive` too, not just `wsActive`: otherwise polling ran alongside
    // WebRTC and every failed request reset the state to `live: false`, putting
    // a "waiting" label over a render that was running fine.
    if (rtcActive || wsActive || !url) return undefined;

    let alive = true;
    const interval = Math.max(20, 1000 / Math.max(1, fps));
    if (!pool.current.length) pool.current = Array.from({ length: POOL }, () => new Image());

    const fail = () => {
      if (!alive) return;
      setLive(false);
      marks.current = [];
      onStatus?.({ live: false, width: null, height: null, fps: null });
      timer.current = setTimeout(request, RETRY_MS);
    };

    const request = () => {
      if (!alive) return;
      const started = performance.now();
      const src = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
      const image = pool.current[slot.current];
      slot.current = (slot.current + 1) % POOL;

      image.onload = () => {
        if (!alive) return;
        setFrame(src);
        setLive(true);
        onStatus?.({
          live: true,
          width: image.naturalWidth,
          height: image.naturalHeight,
          fps: measure(),
          transport: 'HTTP',
        });
        // Subtract the time already spent, or the two would add up.
        timer.current = setTimeout(request, Math.max(0, interval - (performance.now() - started)));
      };
      image.onerror = fail;
      image.src = src;
    };

    request();
    return () => {
      alive = false;
      clearTimeout(timer.current);
      pool.current.forEach((image) => {
        image.onload = null;
        image.onerror = null;
      });
    };
  }, [url, wsActive, rtcActive, fps, onStatus]);

  if (!url && !wsUrl && !signalUrl) {
    return <span className="stage__feed-text">[ FEED TOUCHDESIGNER ]</span>;
  }

  // The <video> stays mounted even with no signal: the effect needs the element
  // reference to attach the stream, and unmounting it when `live` is false would
  // stop the connection from ever starting.
  return (
    <>
      {rtcActive && <video ref={video} autoPlay muted playsInline className="stage__video" />}
      {!live && <span className="stage__feed-text">[ FEED TOUCHDESIGNER — SIGNAL LOST ]</span>}
      {live && !rtcActive && (wsActive ? <canvas ref={canvas} /> : <img src={frame} alt="" />)}
    </>
  );
}
