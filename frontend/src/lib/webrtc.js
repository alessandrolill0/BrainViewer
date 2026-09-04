/** Receives the TouchDesigner render over WebRTC.
 *
 *  WebRTC rather than JPEG frames: every JPEG forced TouchDesigner to read the
 *  image back from the GPU to the CPU, which blocks the pipeline (~70 ms per
 *  frame at 720p, dropping the render from 33 to 10 fps). With WebRTC the
 *  encoding happens on the GPU and the raw pixels never come back.
 *
 *  The video comes directly from TouchDesigner; the backend only relays the
 *  initial handshake (see backend/app/signaling.py). TouchDesigner offers,
 *  since it is the side that has the video.
 */

const RETRY_MS = 3000;

/** No STUN or TURN server: both peers are on the same machine, so host
 *  candidates are enough. Adding one would make an installation that must work
 *  offline depend on the Internet. */
const RTC_CONFIG = { iceServers: [] };

/** Bandwidth ceiling declared to TouchDesigner, in kbit/s.
 *
 *  WebRTC starts cautious and raises the bitrate as it measures the link. A
 *  still image needs very little, so the estimate stays low; as soon as the
 *  brain moves the encoder drops frames instead of exceeding it. Both peers are
 *  on the same machine, so bandwidth is not scarce.
 */
const MAX_KBPS = 20000;

/** Raises the bandwidth ceiling in the video section of the SDP.
 *
 *  `b=AS` must go right after the `m=video` line and its optional `c=`, before
 *  the attributes: anywhere else it is silently ignored.
 *  `x-google-start-bitrate` is a Chrome extension that starts high instead of
 *  ramping up slowly.
 */
function alzaBitrate(sdp) {
  let out = sdp.replace(
    /(m=video[^\r\n]*\r?\n(?:c=[^\r\n]*\r?\n)?)/,
    `$1b=AS:${MAX_KBPS}\r\nb=TIAS:${MAX_KBPS * 1000}\r\n`,
  );
  out = out.replace(
    /(a=fmtp:\d+ [^\r\n]*)/g,
    `$1;x-google-start-bitrate=8000;x-google-max-bitrate=${MAX_KBPS};x-google-min-bitrate=2000`,
  );
  return out;
}

/**
 * Opens the feed and attaches it to a <video> element.
 *
 * @param {string} signalUrl  signaling WebSocket, role `panel`
 * @param {HTMLVideoElement} video  where to attach the stream
 * @param {(state: {phase: string, detail?: string}) => void} onStatus
 * @returns {() => void} teardown function
 */
export function connectFeed(signalUrl, video, onStatus) {
  let closed = false;
  let socket = null;
  let pc = null;
  let retry = null;
  let stats = null;

  const report = (phase, detail) => {
    if (!closed) onStatus?.({ phase, detail });
  };

  const teardown = () => {
    clearInterval(stats);
    if (pc) {
      pc.ontrack = null;
      pc.onicecandidate = null;
      pc.onconnectionstatechange = null;
      pc.close();
      pc = null;
    }
  };

  const send = (message) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  };

  const startPeer = () => {
    teardown();
    pc = new RTCPeerConnection(RTC_CONFIG);

    pc.ontrack = (event) => {
      // Diagnostics: without this, "connected but black" is indistinguishable
      // from "track never arrived" — two faults on two different machines.
      console.info('[webrtc] traccia ricevuta:', event.track.kind,
                   'stato', event.track.readyState,
                   '— flussi', event.streams.length);
      // `srcObject`, not `src`: the stream has no URL, and assigning it
      // directly avoids creating a blob that would need revoking.
      video.srcObject = event.streams[0];
      // Some browsers refuse autoplay even when muted: better to surface that
      // in the state than to sit in front of a black box.
      video.play().catch((err) => report('blocked', String(err.name)));
    };

    pc.onicecandidate = (event) => {
      if (event.candidate) {
        send({
          type: 'candidate',
          candidate: event.candidate.candidate,
          mid: event.candidate.sdpMid,
          index: event.candidate.sdpMLineIndex,
        });
      }
    };

    pc.onconnectionstatechange = () => {
      if (!pc) return;
      console.info('[webrtc] stato connessione:', pc.connectionState);
      if (pc.connectionState === 'connected') {
        report('live');
        avviaStatistiche();
      }
      if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
        report('lost', pc.connectionState);
        // Renegotiate from scratch: a failed connection does not repair
        // itself, and TouchDesigner re-offers as soon as we ask.
        startPeer();
        send({ type: 'request' });
      }
    };
  };

  /** Measured frame rate and resolution, from the stream statistics.
   *
   *  The browser does not expose fps on the <video> element but getStats() does,
   *  and those are the frames actually decoded. It also tells the two causes of
   *  a black box apart: if framesDecoded does not grow, TouchDesigner is
   *  publishing an empty track.
   */
  const avviaStatistiche = () => {
    clearInterval(stats);
    stats = setInterval(async () => {
      if (!pc || closed) return clearInterval(stats);
      const report = await pc.getStats();
      report.forEach((s) => {
        if (s.type === 'inbound-rtp' && s.kind === 'video') {
          onStatus?.({
            phase: 'live',
            fps: Math.round(s.framesPerSecond ?? 0) || null,
            width: s.frameWidth ?? null,
            height: s.frameHeight ?? null,
          });
        }
      });
    }, 1000);
  };

  const handle = async (message) => {
    switch (message.type) {
      case 'peer.joined':
        if (message.role === 'td') {
          startPeer();
          send({ type: 'request' });      // "send me the video"
          report('negotiating');
        }
        break;

      case 'peer.left':
        if (message.role === 'td') {
          teardown();
          report('waiting', 'TD offline');
        }
        break;

      case 'peer.absent':
        report('waiting', 'TD offline');
        break;

      case 'offer': {
        // The SDP declares what the call contains. An `m=video` line is not
        // enough: the direction matters. If TD declares `recvonly` it is asking
        // for video instead of sending it, and `ontrack` never fires even
        // though the connection is established.
        const sezioni = (message.sdp ?? '').split(/^m=/m).slice(1);
        console.info('[webrtc] offerta ricevuta:', sezioni.map((sez) => {
          const tipo = sez.split(' ')[0];
          const dir = (sez.match(/a=(sendrecv|sendonly|recvonly|inactive)/) ?? [])[1];
          return `${tipo}:${dir ?? 'direzione assente'}`;
        }).join(', ') || 'NESSUN MEDIA');
        if (!pc) startPeer();
        await pc.setRemoteDescription({ type: 'offer', sdp: message.sdp });
        const answer = await pc.createAnswer();
        // The SDP is patched before adopting it, so the ceiling applies both
        // locally and in what we send to TouchDesigner.
        const sdp = alzaBitrate(answer.sdp);
        await pc.setLocalDescription({ type: 'answer', sdp });
        send({ type: 'answer', sdp });
        console.info('[webrtc] transceiver dopo la risposta:',
          pc.getTransceivers().map((t) =>
            `${t.receiver?.track?.kind ?? '?'}:${t.currentDirection ?? t.direction}`).join(', '));
        break;
      }

      case 'candidate':
        if (pc && message.candidate) {
          try {
            await pc.addIceCandidate({
              candidate: message.candidate,
              sdpMid: message.mid ?? null,
              sdpMLineIndex: message.index ?? null,
            });
          } catch (err) {
            // A candidate arriving before the remote description is rejected:
            // harmless, more will follow.
            console.debug('candidato ICE scartato', err);
          }
        }
        break;

      default:
        break;
    }
  };

  const connect = () => {
    if (closed) return;
    report('connecting');
    socket = new WebSocket(signalUrl);

    socket.onopen = () => report('waiting', 'in attesa di TD');
    socket.onmessage = (event) => handle(JSON.parse(event.data)).catch((err) =>
      report('error', String(err.message ?? err)));
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      teardown();
      if (!closed) {
        report('offline');
        retry = setTimeout(connect, RETRY_MS);
      }
    };
  };

  connect();

  return () => {
    closed = true;
    clearTimeout(retry);
    clearInterval(stats);
    teardown();
    socket?.close();
    if (video) video.srcObject = null;
  };
}
