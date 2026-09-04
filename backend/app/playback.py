"""Playback commands to TouchDesigner.

TD owns the audio: nothing is decoded or played here, only commands are sent.
The backend keeps an expected state (track, playing, volume) for the UI.
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_SUFFIXES = {'.wav', '.aif', '.aiff', '.mp3', '.flac', '.m4a', '.ogg'}

# Grace period before declaring a track finished. Position is estimated, so it
# is better to stop late than to cut the tail of the track.
END_GRACE = 0.3


class PlaybackError(ValueError):
    """Command rejected before reaching TouchDesigner."""


class PlaybackController:
    def __init__(self, osc_events):
        self.osc = osc_events
        self.track_id: str | None = None
        self.filepath: str | None = None
        self.playing = False
        self.volume = 1.0
        #: track duration if known; without it the end cannot be detected
        self.duration: float | None = None
        # Estimated position: TD does not report where it is actually playing,
        # so we reconstruct it from our own clock. Accurate enough for a mapping
        # that works on sections tens of seconds long.
        self._anchor = 0.0
        self._anchor_at = time.monotonic()

    def position(self) -> float:
        if not self.playing:
            return self._anchor
        return self._anchor + (time.monotonic() - self._anchor_at)

    def _set_position(self, value: float) -> None:
        self._anchor = max(0.0, value)
        self._anchor_at = time.monotonic()

    def load(self, filepath: str, track_id: str | None = None,
             duration: float | None = None) -> dict:
        """Point TD at a file. Does not start playback.

        `duration` in seconds is only used to detect the end of the track.
        """
        path = Path(filepath).expanduser()

        # TD reads the file on its own and would not report a bad path.
        if not path.is_absolute():
            raise PlaybackError(f'il percorso deve essere assoluto: {filepath}')
        if not path.is_file():
            raise PlaybackError(f'file non trovato: {path}')
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            raise PlaybackError(
                f'estensione non riconosciuta: {path.suffix!r} '
                f'(attese: {", ".join(sorted(AUDIO_SUFFIXES))})')

        self.track_id = track_id
        self.filepath = str(path)
        self.playing = False
        self.duration = float(duration) if duration else None
        self._set_position(0.0)
        self.osc.send('/song/loaded', track_id or '', str(path))
        logger.info('Brano caricato in TD: %s', path.name)
        return self.state()

    def play(self) -> dict:
        if self.filepath is None:
            raise PlaybackError('nessun brano caricato')
        self._set_position(self.position())
        self.playing = True
        self.osc.send('/song/play')
        return self.state()

    def pause(self) -> dict:
        self._set_position(self.position())
        self.playing = False
        self.osc.send('/song/pause')
        return self.state()

    def seek(self, position: float) -> dict:
        if self.filepath is None:
            raise PlaybackError('nessun brano caricato')
        if position < 0:
            raise PlaybackError(f'posizione negativa: {position}')
        self._set_position(float(position))
        self.osc.send('/song/seek', float(position))
        return self.state()

    def ended(self) -> bool:
        """True when the playing track has run past its own duration."""
        if not self.playing or not self.duration:
            return False
        return self.position() >= self.duration + END_GRACE

    def finish(self) -> dict:
        """End of track: stop and rewind.

        Unlike pause(), position goes back to 0. The protocol has no "end"
        message: to TD this is simply a track stopped at the beginning.
        """
        self.playing = False
        self._set_position(0.0)
        self.osc.send('/song/pause')
        self.osc.send('/song/seek', 0.0)
        logger.info('Brano finito: %s', self.track_id)
        return self.state()

    def set_volume(self, value: float) -> dict:
        if not 0.0 <= value <= 1.0:
            raise PlaybackError(f'volume fuori range 0-1: {value}')
        self.volume = float(value)
        self.osc.send('/song/volume', self.volume)
        return self.state()

    def state(self) -> dict:
        return {
            'track_id': self.track_id,
            'filepath': self.filepath,
            'playing': self.playing,
            'volume': self.volume,
            'position': round(self.position(), 2),
            'duration': round(self.duration, 2) if self.duration else None,
        }
