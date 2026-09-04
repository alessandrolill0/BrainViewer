#!/usr/bin/env python3
"""Generate the panel's anatomical wireframe from the real region OBJs.

The design called for a drawn asset; here the outline is measured on the same
geometry TouchDesigner instances. If the model ever changes, rerun this script
and the panel stays aligned — a hand drawing would have drifted silently.

It projects each region's vertices onto the sagittal plane (side view, the most
legible for lobes), extracts the concave outline with an alpha shape and writes
the SVG paths to frontend/src/lib/wireframe.js.

    python tools/make_wireframe.py
    python tools/make_wireframe.py --alpha 0.06 --samples 3000
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import Delaunay

ROOT = Path(__file__).resolve().parents[1]
REGIONS_DIR = ROOT / 'touchdesigner' / 'exports' / 'regions'
OUT = ROOT / 'frontend' / 'src' / 'lib' / 'wireframe.js'

#: The lobe_id order, which is also the row order of palette.tsv.
ORDINE = [r['region'] for r in
          sorted(json.loads((REGIONS_DIR / 'regions.json').read_text())['regions'],
                 key=lambda r: r['lobe_id'])]

#: The midline structures stay in the model and the OSC contract but not in the
#: panel: they are internal and a side view would hide them inside the others.
SKIP = {'other_midline'}

#: Vertices to use per region. The outline does not improve past a few thousand
#: points, and triangulating 14,000 costs without gain.
SAMPLES = 2500

#: Maximum circumcircle radius for a triangle to belong to the shape, in model
#: units. Lower = tighter and more jagged; higher = closer to the convex hull.
ALPHA = 0.05


def load_palette() -> dict:
    """Region colours, from the same file TouchDesigner reads.

    palette.tsv holds only `r g b` in 0-1, one row per lobe_id. Reading it here
    instead of copying the values into the frontend is what keeps the panel
    wireframe and the render neurons aligned.
    """
    righe = (REGIONS_DIR / 'palette.tsv').read_text().splitlines()
    colori = {}
    for nome, riga in zip(ORDINE, righe[1:]):        # first row is the header
        r, g, b = (float(v) for v in riga.split())
        colori[nome] = '#%02x%02x%02x' % tuple(round(c * 255) for c in (r, g, b))
    return colori


def load_points(path: Path) -> np.ndarray:
    """Only the `v` lines of the OBJ: vertices are needed here, not faces."""
    coords = []
    for line in path.read_text().splitlines():
        if line.startswith('v '):
            coords.append([float(v) for v in line.split()[1:4]])
    return np.asarray(coords, dtype=float)


def anteroposterior_axis(regions: dict[str, np.ndarray]) -> int:
    """Which axis runs from the front to the back of the head, measured.

    Not assumed: it looks at which axis the frontal and occipital centroids are
    furthest apart on. That is the definition of antero-posterior, and it
    survives a model re-exported with different axes.
    """
    front = regions['frontal'].mean(axis=0)
    back = regions['occipital'].mean(axis=0)
    return int(np.argmax(np.abs(front - back)))


def alpha_shape_edges(points: np.ndarray, alpha: float) -> list[tuple[int, int]]:
    """Border edges of the alpha shape: the concave outline of the cloud.

    A convex hull would wrap the temporal lobe into a potato and lose the
    indentations, which are exactly what makes a brain recognisable. Triangulate,
    drop the triangles that span a gap, and keep the edges belonging to a single
    surviving triangle: by definition, the border.
    """
    tri = Delaunay(points)
    keep = []
    for simplex in tri.simplices:
        a, b, c = points[simplex]
        la, lb, lc = np.linalg.norm(b - a), np.linalg.norm(c - b), np.linalg.norm(a - c)
        s = (la + lb + lc) / 2
        area = max(s * (s - la) * (s - lb) * (s - lc), 1e-12) ** 0.5
        if (la * lb * lc) / (4.0 * area) < alpha:   # circumcircle radius
            keep.append(simplex)

    conteggio: dict[tuple[int, int], int] = defaultdict(int)
    for simplex in keep:
        for i, j in ((0, 1), (1, 2), (2, 0)):
            conteggio[tuple(sorted((simplex[i], simplex[j])))] += 1
    return [edge for edge, n in conteggio.items() if n == 1]


def chain(edges: list[tuple[int, int]]) -> list[list[int]]:
    """Chain the edges into closed rings.

    This writes `M ... L ... Z` instead of a detached segment per edge: a
    continuous path is lighter and is the only one that can be filled. A region
    can produce several rings, and all of them are returned.
    """
    vicini: dict[int, list[int]] = defaultdict(list)
    for a, b in edges:
        vicini[a].append(b)
        vicini[b].append(a)

    visti: set[tuple[int, int]] = set()
    anelli = []
    for start in vicini:
        for first in vicini[start]:
            if tuple(sorted((start, first))) in visti:
                continue
            anello = [start]
            prev, cur = start, first
            visti.add(tuple(sorted((prev, cur))))
            while cur != start:
                anello.append(cur)
                avanti = [n for n in vicini[cur]
                          if n != prev and tuple(sorted((cur, n))) not in visti]
                if not avanti:
                    break            # open chain: close it as it is
                prev, cur = cur, avanti[0]
                visti.add(tuple(sorted((prev, cur))))
            if len(anello) > 8:      # drop fragments, they are noise
                anelli.append(anello)
    return anelli


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alpha', type=float, default=ALPHA)
    parser.add_argument('--samples', type=int, default=SAMPLES)
    args = parser.parse_args()

    ordine = ORDINE
    punti = {nome: load_points(REGIONS_DIR / f'{nome}.obj') for nome in ordine}
    colori = load_palette()

    ap = anteroposterior_axis(punti)
    print(f'asse antero-posteriore: {"XYZ"[ap]}  (misurato su frontale vs occipitale)')

    # Side view: antero-posterior horizontal, height vertical. SVG X grows to
    # the right and Y downwards, so the height must be flipped or the brain
    # comes out upside down.
    tutti = np.vstack(list(punti.values()))
    x0, x1 = tutti[:, ap].min(), tutti[:, ap].max()
    y0, y1 = tutti[:, 1].min(), tutti[:, 1].max()
    scala = 1000.0 / max(x1 - x0, y1 - y0)
    larghezza, altezza = (x1 - x0) * scala, (y1 - y0) * scala

    rng = np.random.default_rng(7)     # seed fisso: due esecuzioni, stesso file
    tracciati = {}
    for nome in ordine:
        if nome in SKIP:
            continue
        p = punti[nome]
        if len(p) > args.samples:
            p = p[rng.choice(len(p), args.samples, replace=False)]
        xy = np.column_stack([(p[:, ap] - x0) * scala, altezza - (p[:, 1] - y0) * scala])

        anelli = chain(alpha_shape_edges(xy, args.alpha * scala))
        d = ' '.join(
            'M ' + ' L '.join(f'{xy[i][0]:.1f},{xy[i][1]:.1f}' for i in anello) + ' Z'
            for anello in anelli)
        tracciati[nome] = d
        print(f'  {nome:<14} {len(anelli)} anelli, {len(d) // 1024} KB')

    OUT.write_text(
        '/* GENERATED by tools/make_wireframe.py — do not edit by hand.\n'
        ' * Outlines measured on the real region OBJs, side view.\n'
        ' * Midline structures excluded: internal, hidden in this view. */\n\n'
        f'export const VIEW_BOX = "0 0 {larghezza:.0f} {altezza:.0f}";\n\n'
        'export const OUTLINES = ' + json.dumps(tracciati, indent=2) + ';\n\n'
        '/* The same colours TouchDesigner gives the neurons: read from\n'
        ' * palette.tsv, not copied by hand. */\n'
        'export const COLORS = ' + json.dumps(
            {k: v for k, v in colori.items() if k in tracciati}, indent=2) + ';\n')
    print(f'\nscritto {OUT.relative_to(ROOT)}  ({OUT.stat().st_size // 1024} KB)')


if __name__ == '__main__':
    main()
