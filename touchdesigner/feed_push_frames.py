"""Execute DAT: pushes frames to the connected WebSocket clients.

The active half of the feed. The Web Server DAT holds the connection and the
client list (feed_web_server.py); this decides when to send, and sends.

Not on every TouchDesigner frame: encoding and shipping more than necessary
steals time from the render. JPEG encoding involves a GPU-to-CPU readback that
blocks the pipeline — at 1280x720 and 25 fps the render dropped from 33 to
10 fps with the panel open. The two knobs are TARGET_FPS here and the resolution
of the source TOP (SOURCE_TOP in feed_web_server.py).

Usage: an Execute DAT with the Frame Start callback enabled, holding this
content. See docs/touchdesigner_setup.md.
"""

# Operators involved.
WEB_SERVER = 'webserver1'

# Frames per second sent to the panel. Raise it only if the render has frames
# to spare.
TARGET_FPS = 15


def onFrameStart(frame):
    server = op(WEB_SERVER)
    if server is None:
        return

    # Pacing: frames are skipped based on the project's real frame rate, so the
    # value stays correct if that changes.
    every = max(1, int(round(max(1.0, project.cookRate) / TARGET_FPS)))
    if frame % every:
        return

    # `store` on an operator is a method: read with fetch().
    clients = server.fetch('feed_clients', [])
    if not clients:
        return          # nobody is watching: do not even encode

    # The Callbacks DAT holds encode_frame(). Depending on the build the
    # parameter returns the operator or its path, so it is normalised.
    reference = server.par.callbacks.eval()
    callbacks = reference if hasattr(reference, 'module') else op(reference)
    if callbacks is None:
        debug('feed: Callbacks DAT non trovato sul Web Server')
        return

    data = callbacks.module.encode_frame()
    if not data:
        return

    for client in list(clients):
        try:
            server.webSocketSendBinary(client, data)
        except TypeError:
            # Some builds send to all clients, with no recipient.
            server.webSocketSendBinary(data)
            break
        except Exception as exc:
            # A client lost between frames must not stop the others: drop it
            # from the list and carry on.
            debug(f'feed: invio fallito, client rimosso ({exc})')
            if client in clients:
                clients.remove(client)
    return


def onStart():
    return


def onCreate():
    return


def onExit():
    return


def onFrameEnd(frame):
    return


def onPlayStateChange(state):
    return


def onDeviceChange():
    return


def onProjectPreSave():
    return


def onProjectPostSave():
    return
