"""Library of user-uploaded tracks.

Files on disk plus a JSON index; no database, since tracks number in the dozens.
Files are stored as `<track_id>.<ext>` rather than under their original name:
that path travels inside an OSC message that TouchDesigner has to reopen, so it
must stay free of spaces and accents. The real name is kept in the metadata.
"""

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import mutagen

logger = logging.getLogger(__name__)

AUDIO_SUFFIXES = {'.wav', '.aif', '.aiff', '.mp3', '.flac', '.m4a', '.ogg'}
CHUNK = 1 << 20


class LibraryError(ValueError):
    """Invalid library request."""


def _probe_duration(path: Path) -> float | None:
    """Duration in seconds, or None if the format cannot be read."""
    try:
        info = mutagen.File(path)
        if info is not None and info.info is not None:
            return round(float(info.info.length), 3)
    except Exception as exc:  # mutagen raises anything on corrupt files
        logger.warning('durata non leggibile per %s: %s', path.name, exc)
    return None


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / 'library.json'
        self._tracks: list[dict] = self._load()

    # --- persistence ---------------------------------------------------

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.index_path.read_text())['tracks']
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # Better to start empty than to fail at boot: the audio files are
            # still on disk and the broken index is set aside.
            broken = self.index_path.with_suffix('.json.broken')
            logger.error('indice illeggibile (%s), spostato in %s', exc, broken.name)
            self.index_path.replace(broken)
            return []

    def _save(self) -> None:
        # Atomic write: a crash must not leave a truncated index.
        tmp = self.index_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({'tracks': self._tracks}, indent=2, ensure_ascii=False))
        tmp.replace(self.index_path)

    # --- API -----------------------------------------------------------

    def list(self) -> list[dict]:
        return list(self._tracks)

    def get(self, track_id: str) -> dict:
        for track in self._tracks:
            if track['track_id'] == track_id:
                return track
        raise LibraryError(f'track non trovata: {track_id}')

    def add_file(self, source: Path, original_name: str) -> dict:
        """Take over a file already written to disk and move it into the library."""
        suffix = Path(original_name).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            source.unlink(missing_ok=True)
            raise LibraryError(
                f'estensione non supportata: {suffix or "(nessuna)"} '
                f'(attese: {", ".join(sorted(AUDIO_SUFFIXES))})')

        digest = hashlib.sha256()
        with source.open('rb') as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)
        checksum = digest.hexdigest()

        # Same content already present: return the existing track.
        for track in self._tracks:
            if track['sha256'] == checksum:
                source.unlink(missing_ok=True)
                logger.info('%s e gia in libreria come %s', original_name, track['track_id'])
                return {**track, 'duplicate': True}

        track_id = uuid.uuid4().hex[:12]
        destination = self.root / f'{track_id}{suffix}'
        shutil.move(str(source), destination)

        track = {
            'track_id': track_id,
            'title': Path(original_name).stem,
            'filename': original_name,
            'filepath': str(destination.resolve()),
            'duration': _probe_duration(destination),
            'size_bytes': destination.stat().st_size,
            'sha256': checksum,
            'added_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'analysis_status': 'pending',
        }
        self._tracks.append(track)
        self._save()
        logger.info('Aggiunto in libreria: %s (%s)', original_name, track_id)
        return track

    def delete(self, track_id: str) -> dict:
        track = self.get(track_id)
        Path(track['filepath']).unlink(missing_ok=True)
        self._tracks = [t for t in self._tracks if t['track_id'] != track_id]
        self._save()
        logger.info('Rimosso dalla libreria: %s', track_id)
        return track

    def set_analysis(self, track_id: str, status: str,
                     analysis: dict | None = None, error: str | None = None) -> dict:
        """Update analysis outcome. status: pending | running | done | error."""
        track = self.get(track_id)
        track['analysis_status'] = status
        if analysis is not None:
            track['analysis'] = analysis
            # The analysis knows the duration better than the file metadata.
            if analysis.get('duration'):
                track['duration'] = analysis['duration']
        if error is not None:
            track['analysis_error'] = error
        elif status != 'error':
            track.pop('analysis_error', None)
        self._save()
        return track

    def rename(self, track_id: str, title: str) -> dict:
        title = title.strip()
        if not title:
            raise LibraryError('il titolo non puo essere vuoto')
        track = self.get(track_id)
        track['title'] = title
        self._save()
        return track
