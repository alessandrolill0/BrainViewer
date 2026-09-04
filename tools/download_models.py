"""Download the Essentia TensorFlow models used for mood.

The .pb files are not bundled with essentia-tensorflow and come from the
official Essentia model zoo. They land in backend/models/ and are excluded from
git, being binaries regenerable with this script.

Usage:
    conda activate brain_viewer
    python tools/download_models.py

Mood uses two cascaded models: msd-musicnn extracts embeddings from the audio,
emomusic-msd-musicnn predicts valence and arousal from them. Without the files
the analysis still works, falling back to the heuristic in
backend/app/analysis.py.
"""

import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://essentia.upf.edu/models'
DEST = Path(__file__).resolve().parents[1] / 'backend' / 'models'

# MediaPipe Face Landmarker: 478 landmarks plus 52 blendshapes (the facial
# actions). Used by the SUBJECT / STATE cell of the panel. A .task is a
# self-contained archive, hence a single file.
MEDIAPIPE = 'https://storage.googleapis.com/mediapipe-models/face_landmarker'

MODELS = [
    (f'{BASE}/feature-extractors/musicnn/msd-musicnn-1.pb', 'msd-musicnn-1.pb'),
    (f'{BASE}/classification-heads/emomusic/emomusic-msd-musicnn-1.pb',
     'emomusic-msd-musicnn-1.pb'),
    (f'{BASE}/classification-heads/emomusic/emomusic-msd-musicnn-1.json',
     'emomusic-msd-musicnn-1.json'),
    (f'{MEDIAPIPE}/face_landmarker/float16/1/face_landmarker.task',
     'face_landmarker.task'),
]


def download(url: str, dest: Path) -> bool:
    if dest.exists():
        print(f'  {dest.name}: gia presente ({dest.stat().st_size / 1e6:.1f} MB)')
        return True
    print(f'  {dest.name}: scarico da {url}')
    tmp = dest.with_suffix(dest.suffix + '.part')
    try:
        with urllib.request.urlopen(url, timeout=120) as response, tmp.open('wb') as out:
            total = int(response.headers.get('Content-Length', 0))
            read = 0
            while chunk := response.read(1 << 16):
                out.write(chunk)
                read += len(chunk)
                if total:
                    print(f'\r    {read / 1e6:6.1f} / {total / 1e6:.1f} MB', end='')
            print()
        tmp.replace(dest)  # rinomina solo a download completo: niente file monchi
        return True
    except urllib.error.HTTPError as exc:
        print(f'    FALLITO: HTTP {exc.code}')
    except Exception as exc:
        print(f'    FALLITO: {type(exc).__name__}: {exc}')
    tmp.unlink(missing_ok=True)
    return False


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f'Modelli in {DEST}')
    ok = [download(url, DEST / name) for url, name in MODELS]

    # The .json is only metadata: missing it is not a problem.
    essential = ok[:2]
    if all(essential):
        print('\nModelli pronti: il mood usera i modelli pre-addestrati.')
        return 0
    print('\nQualche modello manca: l\'analisi ricadra sull\'euristica.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
