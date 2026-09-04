"""OSC In DAT callbacks: translate /song/* commands into audio parameters.

See docs/touchdesigner_audio.md. In short, the *Callbacks DAT* parameter of the
OSC In DAT must point to a Text DAT holding this content.

Note the parameter names below (`file`, `play`, `cuepoint`, ...): they are the
Python names of the Audio File In CHOP parameters and can change between builds.
Middle-click a parameter in the panel to check its Python name.
"""

# Name of the Audio File In CHOP to drive. Change it if yours differs.
AUDIO_OP = 'audiofilein1'


def onReceiveOSC(dat, rowIndex, message, bytes, timeStamp, address, args, peer):
    audio = op(AUDIO_OP)
    if audio is None:
        debug(f'song_osc: operatore {AUDIO_OP!r} non trovato')
        return

    if address == '/song/loaded':
        # args = [track_id, filepath]. The path is absolute and local: backend
        # and TouchDesigner run on the same machine.
        filepath = args[1] if len(args) > 1 else args[0]
        audio.par.file = filepath
        audio.par.play = 0
        audio.par.cuepoint = 0
        audio.par.cuepulse.pulse()
        debug(f'song_osc: caricato {filepath}')

    elif address == '/song/play':
        audio.par.play = 1

    elif address == '/song/pause':
        audio.par.play = 0

    elif address == '/song/seek':
        audio.par.cuepoint = float(args[0])
        audio.par.cuepulse.pulse()

    elif address == '/song/volume':
        audio.par.volume = float(args[0])

    else:
        debug(f'song_osc: indirizzo ignorato {address}')
