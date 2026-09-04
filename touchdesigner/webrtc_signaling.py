"""WebSocket DAT: signaling dialogue with the backend.

No video passes through here: only the descriptions (offer/answer) and the
addresses (candidate) the two peers need to find each other. Once established,
the video goes from TouchDesigner to the browser directly.

    TD  <--this WebSocket-->  backend  <--WebSocket-->  panel
    TD  <========== direct WebRTC video ===========>  panel

Setup: a WebSocket DAT pointing at the backend with role `td`. The WebSocket DAT
has no parameter for the path, only Network Address and Network Port, so the
path goes inside the address:

    Network Address: localhost/ws/signal/td
    Network Port:    8000

This script goes in the Callbacks DAT of the WebSocket DAT; the WebRTC DAT
callbacks are in webrtc_callbacks.py. Two distinct DATs.

TouchDesigner offers, being the side that has the video. The panel just shows
up, and the backend announces it with `peer.joined`. The panel can also ask
explicitly with `request`, which is needed to renegotiate a failed connection.
"""

import json

# Operators involved.
WEBRTC = 'webrtc1'
STREAM_OUT = 'videostreamout1'

#: Video track name. A free label that only has to exist: with an empty field
#: no track is created and negotiation never starts.
VIDEO_TRACK = 'video'

#: Storage key on the WebRTC DAT holding the current connection id. Kept there
#: and not in a global because TouchDesigner reloads the module on every text
#: change, and a global would reset, orphaning the connection.
CONN_KEY = 'webrtc_connection'

#: Table rows before opening, and how many frames have been waited. Used by
#: finish_connection, which runs one or more frames after start_connection.
PENDING_KEY = 'webrtc_pending'
ATTEMPT_KEY = 'webrtc_attempts'

#: Which connection the offer was already requested for, so it is not requested
#: twice: a second offer would cancel the negotiation in progress.
OFFERED_KEY = 'webrtc_offered'

#: How many frames to wait for the id before giving up. One is enough on the
#: same machine; the margin covers a struggling TouchDesigner.
MAX_ATTEMPTS = 10


def _webrtc():
    return op(WEBRTC)


def _send(dat, message: dict) -> None:
    dat.sendText(json.dumps(message))


def _ids_dalla_tabella(web) -> list:
    """Connection ids listed in the WebRTC DAT table.

    The DAT is a table: every open connection appears as a row. They are
    recognised by their UUID shape, so the header and the status columns are not
    mistaken for ids.
    """
    ids = []
    for riga in range(web.numRows):
        cella = web[riga, 0]
        if cella is None:
            continue
        valore = str(cella.val).strip()
        if len(valore) >= 8 and valore.count('-') >= 4:
            ids.append(valore)
    return ids


def _attach_track(connection_id: str) -> bool:
    """Attach the Video Stream Out TOP to the freshly opened connection.

    The four parameters have different roles despite similar names:

        webrtc            -> reference to the WebRTC DAT
        webrtcconnection  -> connection id
        webrtcvideotrack  -> NAME of the video track, a free string
        webrtcaudiotrack  -> same for audio

    The two track names must not be filled with the DAT name: that produces an
    attachment that looks fine and negotiates nothing.

    Order matters: the DAT and the track names first, the connection LAST. It is
    assigning the connection that starts everything, and it must find the rest
    already in place.
    """
    top = op(STREAM_OUT)
    if top is None:
        debug(f'webrtc: {STREAM_OUT} non trovato')
        return False

    if not top.inputs:
        debug(f'webrtc: {STREAM_OUT} non ha nessun ingresso video')
    if hasattr(top.par, 'active') and not top.par.active.eval():
        debug(f'webrtc: {STREAM_OUT} non e\' Active')

    if not hasattr(top.par, 'webrtcconnection'):
        debug(f'webrtc: {STREAM_OUT} non ha i parametri WebRTC: il Mode e\' giusto?')
        return False

    # A terminal TOP in TouchDesigner does not cook: with nothing downstream it
    # sits still, and a still Video Stream Out never registers its track on the
    # connection. The offer then goes out `recvonly`, the connection establishes
    # anyway, and the panel stays black without a single error.
    if hasattr(top.par, 'cooktype'):
        top.par.cooktype = 'always'

    top.par.webrtc = WEBRTC
    if hasattr(top.par, 'webrtcvideotrack'):
        top.par.webrtcvideotrack = VIDEO_TRACK
    # Audio does not travel over WebRTC: TouchDesigner plays it through the
    # sound card and the visitor sits in front of the screen. An extra audio
    # track would be wasted bandwidth and one more way to fail.
    if hasattr(top.par, 'webrtcaudiotrack'):
        top.par.webrtcaudiotrack = ''

    top.par.webrtcconnection = connection_id
    debug(f'webrtc: {STREAM_OUT} agganciato alla connessione {connection_id[:8]}...')
    # All the node parameters: when the track does not start, the cause is
    # almost always a switch assumed to be on. Wrapped in try/except on purpose:
    # diagnostics that raise would block the real work — the offer once stopped
    # firing because the exception left this function before `return True`.
    try:
        valori = []
        for par in top.pars():
            pagina = getattr(par.page, 'name', '') or ''
            if pagina.lower() != 'common':
                valori.append(f'{par.name}={par.eval()}')
        debug(f'webrtc: parametri di {STREAM_OUT} -> ' + ', '.join(valori))
    except Exception as exc:
        debug(f'webrtc: elenco parametri non riuscito ({exc})')
    return True


def _add_track(web, connection_id: str) -> bool:
    """Explicitly add the video track to the connection.

    Setting the Video Stream Out parameters is not enough: the connection stays
    without local tracks and the offer goes out `recvonly`, i.e. TouchDesigner
    asks for video instead of sending it. It establishes anyway and the panel
    stays black with no error anywhere.

    The signature changes between builds, so the plausible forms are tried and
    the one that worked is logged.
    """
    if not hasattr(web, 'addTrack'):
        debug('webrtc: il WebRTC DAT non ha addTrack')
        return False

    tentativi = (
        ('addTrack(conn, nome)', lambda: web.addTrack(connection_id, VIDEO_TRACK)),
        ('addTrack(conn, "video", nome)', lambda: web.addTrack(connection_id, 'video', VIDEO_TRACK)),
        ('addTrack(conn)', lambda: web.addTrack(connection_id)),
    )
    for descrizione, prova in tentativi:
        try:
            prova()
            debug(f'webrtc: traccia aggiunta con {descrizione}')
            return True
        except Exception as exc:
            debug(f'webrtc: {descrizione} -> {exc}')
    return False


def create_offer() -> None:
    """Request the offer, a couple of frames after attaching the video.

    In theory attaching the track is enough and onNegotiationNeeded fires by
    itself. In this build it does not, and the connection would sit open doing
    nothing forever, so the offer is requested explicitly.

    Not immediately: the track joins the connection on the next cook, like the
    id, and offering in the same instant would produce an SDP with no video.
    """
    web = _webrtc()
    if web is None:
        return
    connection_id = web.fetch(CONN_KEY, None)
    if not connection_id:
        return
    if web.fetch(OFFERED_KEY, None) == connection_id:
        return          # already offered for this connection
    _add_track(web, connection_id)
    web.store(OFFERED_KEY, connection_id)
    web.createOffer(connection_id)


def start_connection(dat) -> None:
    """Open a connection. The id and the video attachment come later.

    Two limits of this build: openConnection() takes no arguments and returns no
    id. The id only appears in the WebRTC DAT table, which updates on the next
    cook, not inside this call — reading it immediately returns just the header.

    So the connection is opened here and the id collected one frame later in
    finish_connection. That is also why createOffer cannot live in this function.
    """
    web = _webrtc()
    if web is None:
        debug(f'webrtc: {WEBRTC} non trovato')
        return

    if hasattr(web.par, 'active') and not web.par.active.eval():
        debug('webrtc: il WebRTC DAT non e\' Active, non aprira\' niente')
        return

    close_connection()
    web.store(OFFERED_KEY, None)
    web.store(PENDING_KEY, list(_ids_dalla_tabella(web)))
    web.store(ATTEMPT_KEY, 0)
    web.openConnection()
    _rimanda_raccolta()


def _rimanda_raccolta() -> None:
    """Call finish_connection on the next frame."""
    run(f"op({me.path!r}).module.finish_connection()", delayFrames=1)


def finish_connection() -> None:
    """Collect the id that appeared in the table and attach the video."""
    web = _webrtc()
    if web is None:
        return

    prima = set(web.fetch(PENDING_KEY, []) or [])
    nuovi = [c for c in _ids_dalla_tabella(web) if c not in prima]

    if not nuovi:
        tentativi = int(web.fetch(ATTEMPT_KEY, 0)) + 1
        web.store(ATTEMPT_KEY, tentativi)
        if tentativi <= MAX_ATTEMPTS:
            _rimanda_raccolta()
            return
        debug(f'webrtc: nessun id dopo {MAX_ATTEMPTS} fotogrammi. Tabella del DAT:')
        for riga in range(web.numRows):
            debug(f'  riga {riga}: {[str(c.val) for c in web.row(riga)]}')
        return

    connection_id = nuovi[-1]
    web.store(CONN_KEY, connection_id)
    if _attach_track(connection_id):
        # Five frames and not two: the track joins the connection once the TOP
        # has cooked at least once, and with the Cook Type just changed it is
        # worth giving it room. Offering too early produces a `recvonly` SDP.
        run(f"op({me.path!r}).module.create_offer()", delayFrames=5)


def close_connection() -> None:
    web = _webrtc()
    if web is None:
        return
    connection_id = web.fetch(CONN_KEY, None)
    if connection_id:
        try:
            web.closeConnection(connection_id)
        except Exception as exc:
            debug(f'webrtc: chiusura fallita ({exc})')
    web.store(CONN_KEY, None)


# --- WebSocket DAT callbacks --------------------------------------------

def onConnect(dat):
    debug('webrtc: segnalazione connessa al backend')
    return


def onDisconnect(dat):
    # Without the relay nothing can be negotiated: close and wait for the
    # WebSocket DAT to retry on its own.
    debug('webrtc: segnalazione caduta')
    close_connection()
    return


def onReceiveText(dat, rowIndex, message):
    try:
        data = json.loads(message)
    except ValueError:
        debug(f'webrtc: messaggio non JSON scartato ({message[:60]})')
        return

    tipo = data.get('type')
    web = _webrtc()
    connection_id = web.fetch(CONN_KEY, None) if web else None

    if tipo == 'peer.joined':
        # The panel showed up: offer the video.
        if data.get('role') == 'panel':
            start_connection(dat)

    elif tipo == 'request':
        # Explicit request from the panel, which also arrives right after
        # `peer.joined`. Only restart if no connection is already in progress,
        # otherwise each panel opening would create two or three and the last
        # would cancel the negotiation of the previous ones.
        if not connection_id:
            start_connection(dat)

    elif tipo == 'peer.left':
        if data.get('role') == 'panel':
            debug('webrtc: il pannello se ne e\' andato')
            close_connection()

    elif tipo == 'answer':
        if connection_id:
            web.setRemoteDescription(connection_id, 'answer', data.get('sdp', ''))
            debug('webrtc: risposta del pannello accettata')

    elif tipo == 'candidate':
        if connection_id and data.get('candidate'):
            web.addIceCandidate(connection_id, data['candidate'],
                                data.get('index') or 0, data.get('mid') or '')

    elif tipo == 'peer.absent':
        debug('webrtc: nessun pannello aperto')
        close_connection()

    return


def onReceiveBinary(dat, contents):
    return


def onReceivePing(dat, contents):
    dat.sendPong(contents)
    return


def onReceivePong(dat, contents):
    return


def onMonitorMessage(dat, message):
    return
