"""Web Server DAT callbacks: handle WebSocket clients and serve a JPEG.

Two channels on the same port. WebSocket is the fast path: TouchDesigner pushes
frames to connected clients at its own pace, with no request per frame; the
actual sending happens in feed_push_frames.py on an Execute DAT. HTTP is the
fallback and the diagnostics: a GET returns a single frame, so the chain can be
checked from any browser at http://localhost:9980.

See docs/touchdesigner_setup.md. In short, the *Callbacks DAT* parameter of the
Web Server DAT points to a Text DAT holding this content, with *Network Port*
set to 9980.
"""

# TOP to publish: the Null after the Render, or better a Resolution TOP
# downstream, so no more pixels are encoded than the panel shows.
# It must point at a resized TOP, not the full-resolution render: saveByteArray
# forces the GPU to hand the image back to the CPU, and that readback blocks the
# pipeline. The cost scales with pixels — at 1280x720 the project dropped from
# 33 to 10 fps with the panel open.
SOURCE_TOP = 'res_feed'

# JPEG quality. Raising it is nearly free: the cost is the GPU readback, which
# depends on pixels and not on quality. High is needed here — bright neurons on
# a black background with soft halos are the worst case for JPEG, and at 0.6 the
# artefacts around lit areas look like a fault in the render.
QUALITY = 0.85

# Connected WebSocket clients. They live in the DAT storage and not in a global
# variable: TouchDesigner reloads the module on every text change, and a global
# would silently reset, disconnecting everyone.
# Note: on an operator `store` is a METHOD, not a dict — read with
# fetch(key, default), write with store(key, value).
CLIENTS_KEY = 'feed_clients'


def _clients(dat):
    """List of connected clients, created on first access."""
    return dat.fetch(CLIENTS_KEY, [], storeDefault=True)


def encode_frame():
    """Current frame as JPEG, or None if the source is missing."""
    source = op(SOURCE_TOP)
    if source is None:
        return None
    try:
        return source.saveByteArray('.jpg', quality=QUALITY)
    except TypeError:
        # Older builds do not accept the quality parameter.
        return source.saveByteArray('.jpg')


def onWebSocketOpen(webServerDAT, client, uri):
    clients = _clients(webServerDAT)
    if client not in clients:
        clients.append(client)
    debug(f'feed: client connesso ({len(clients)} attivi)')
    return


def onWebSocketClose(webServerDAT, client):
    clients = _clients(webServerDAT)
    if client in clients:
        clients.remove(client)
    debug(f'feed: client disconnesso ({len(clients)} attivi)')
    return


def onWebSocketReceiveText(webServerDAT, client, data):
    return


def onWebSocketReceiveBinary(webServerDAT, client, data):
    return


def onHTTPRequest(webServerDAT, request, response):
    frame = encode_frame()

    if frame is None:
        response['statusCode'] = 404
        response['statusReason'] = 'Not Found'
        response['Content-Type'] = 'text/plain'
        response['data'] = f'TOP {SOURCE_TOP} non trovato'
        return response

    response['statusCode'] = 200
    response['statusReason'] = 'OK'
    response['Content-Type'] = 'image/jpeg'
    # Without this the browser would reuse the first frame forever.
    response['Cache-Control'] = 'no-store'
    response['Access-Control-Allow-Origin'] = '*'
    response['data'] = frame
    return response


def onServerStart(webServerDAT):
    webServerDAT.store(CLIENTS_KEY, [])
    debug(f'feed: server avviato, sorgente {SOURCE_TOP}')
    return


def onServerStop(webServerDAT):
    webServerDAT.store(CLIENTS_KEY, [])
    return
