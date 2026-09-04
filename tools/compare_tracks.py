#!/usr/bin/env python3
"""Compare lobe activation across the tracks in the library.

Divisive normalization and synchrony exist to make the difference between
tracks visible; looking at the brain and saying "it seems different" is not a
check. This script samples the mapping along the whole track and prints the
numbers.

It touches neither TouchDesigner nor playback: it reads the analysis already
stored in the library and calls the same functions the backend runs live.

    python tools/compare_tracks.py
    python tools/compare_tracks.py --step 0.5
"""

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app import mapping  # noqa: E402  (dopo il sys.path)

LIBRARY = ROOT / 'backend' / 'uploads' / 'library.json'


def load_tracks() -> list[dict]:
    if not LIBRARY.exists():
        sys.exit(f'libreria non trovata: {LIBRARY}')
    data = json.loads(LIBRARY.read_text())
    tracks = data['tracks'] if isinstance(data, dict) else data
    done = [t for t in tracks if t.get('analysis_status') == 'done']
    if not done:
        sys.exit('nessun brano analizzato: carica qualcosa e aspetta l\'analisi')
    return done


def profile(track: dict, step: float) -> dict:
    """Mean activation along the whole track, plus synchrony.

    The pose is excluded (`pose=None`), so the occipital lobe stays at rest:
    what is measured here is what the music does, and including it would make
    the comparison depend on how much the person moved.
    """
    analysis = track['analysis']
    duration = float(analysis.get('duration') or track.get('duration') or 0)
    positions = [i * step for i in range(int(duration / step) or 1)]

    somme = [0.0] * len(mapping.REGION_ORDER)
    sync_somma = 0.0
    for pos in positions:
        for i, value in enumerate(mapping.activation(analysis, pos)):
            somme[i] += value
        sync_somma += mapping.synchrony(analysis, pos)

    n = len(positions)
    medie = [s / n for s in somme]
    musicali = [medie[mapping.REGION_ORDER.index(r)] for r in mapping.MUSICAL_REGIONS]
    return {
        'titolo': track.get('title', track['track_id']),
        'medie': medie,
        'sync': sync_somma / n,
        'spread': max(musicali) - min(musicali),
        'dominante': mapping.MUSICAL_REGIONS[musicali.index(max(musicali))],
        'bpm': analysis.get('bpm'),
        'sezioni': [s['label'] for s in analysis.get('sections') or []],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step', type=float, default=1.0,
                        help='passo di campionamento in secondi (default 1)')
    args = parser.parse_args()

    profili = [profile(t, args.step) for t in load_tracks()]

    intestazione = f"{'brano':<22}" + ''.join(f'{r[:5]:>8}' for r in mapping.REGION_ORDER)
    intestazione += f"{'sync':>8}{'spread':>8}"
    print(intestazione)
    print('-' * len(intestazione))
    for p in profili:
        print(f"{p['titolo'][:21]:<22}"
              + ''.join(f'{v:>8.2f}' for v in p['medie'])
              + f"{p['sync']:>8.2f}{p['spread']:>8.2f}")

    print('\nDominante e struttura:')
    for p in profili:
        print(f"  {p['titolo'][:21]:<22} {p['bpm']:>6.1f} BPM   dominante: "
              f"{p['dominante']:<12} {' → '.join(p['sezioni'])}")

    if len(profili) < 2:
        return

    print('\nDistanza fra i profili (media delle differenze sui 5 lobi musicali):')
    for a, b in combinations(profili, 2):
        diff = {}
        for regione in mapping.MUSICAL_REGIONS:
            i = mapping.REGION_ORDER.index(regione)
            diff[regione] = abs(a['medie'][i] - b['medie'][i])
        distanza = sum(diff.values()) / len(diff)
        peggiore = max(diff, key=diff.get)
        print(f"  {a['titolo'][:18]:<20} vs {b['titolo'][:18]:<20} "
              f"{distanza:.3f}   (piu' diverso: {peggiore} {diff[peggiore]:.2f}, "
              f"sync {abs(a['sync'] - b['sync']):.2f})")

    print("\nRiferimento: sotto 0.10 i due brani si somigliano troppo, "
          "sopra 0.20 si distinguono a colpo d'occhio.")


if __name__ == '__main__':
    main()
